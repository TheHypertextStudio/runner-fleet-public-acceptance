#!/usr/bin/env python3
"""Bounded GitHub-hosted recovery. Standalone: never imports repository code."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request


class Failure(Exception):
    """A controlled error; provider response bodies and credentials stay private."""


class ObservationUnavailable(Failure):
    """A transient read failure; never authorizes repeating a mutation."""


def require(okay, message):
    if not okay:
        raise Failure(message)


SEGMENT_SECONDS=40*60
SOURCE_LIFETIME_SECONDS=35*24*60*60
RECOVERY_WORKFLOW='hypertext-recovery.yml'
MANIFEST_PATH='.github/hypertext/recovery.json'


def startup_read(api,path,*,sleep=time.sleep,attempts=6):
    """Bounded GET-only startup retry; never retry an uncertain write."""
    for attempt in range(attempts):
        try:return api('GET',path)
        except ObservationUnavailable:
            if attempt==attempts-1:raise
            sleep(5)
    raise Failure('Recovery startup observation unavailable.')


def validate_manifest(value):
    require(isinstance(value,dict) and set(value)=={'schema_version','workflows'}
            and type(value['schema_version']) is int and value['schema_version']==1
            and isinstance(value['workflows'],list) and 0<len(value['workflows'])<=128,
            'Recovery manifest is invalid.')
    seen=set()
    for entry in value['workflows']:
        require(isinstance(entry,dict) and set(entry)=={'path','sha256'}
                and isinstance(entry['path'],str) and re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml',entry['path'])
                and isinstance(entry['sha256'],str) and re.fullmatch(r'[a-f0-9]{64}',entry['sha256'])
                and entry['path'] not in seen,'Recovery manifest workflow is invalid or repeated.')
        seen.add(entry['path'])
    return value


def content_bytes(value):
    require(isinstance(value,dict) and value.get('encoding')=='base64'
            and isinstance(value.get('content'),str) and len(value['content'])<=2*1024*1024,
            'Recovery source content is unavailable or unsupported.')
    raw=base64.b64decode(''.join(value['content'].split()),validate=True)
    require(len(raw)<=1024*1024,'Recovery source content exceeds its bound.')
    return raw


def review_manifest(current,api,repo,revision,current_revision):
    """Historical trusted-main manifests are data, never executable source."""
    validate_manifest(current)
    require(all(isinstance(v,str) and re.fullmatch(r'[a-f0-9]{40}',v) for v in (revision,current_revision)),
            'Recovery review revision is invalid.')
    if revision==current_revision:return current
    compared=startup_read(api,f'repos/{repo}/compare/{revision}...{current_revision}')
    require(isinstance(compared,dict) and compared.get('status') in ('ahead','identical')
            and compared.get('merge_base_commit',{}).get('sha')==revision,
            'Recovery review is not an ancestor of the current trusted main workflow.')
    historical=startup_read(api,f'repos/{repo}/contents/{MANIFEST_PATH}?ref={revision}')
    return validate_manifest(json.loads(content_bytes(historical)))


def contract(reviewed,current,workflow):
    validate_manifest(reviewed);validate_manifest(current)
    require(any(entry['path']==workflow for entry in current['workflows']),
            'Workflow is no longer enrolled for infrastructure recovery.')
    match=next((entry for entry in reviewed['workflows'] if entry['path']==workflow),None)
    require(match is not None,'Workflow lacks its original reviewed recovery contract.')
    return match


def remaining_lifetime(source,*,now=None):
    created=source.get('created_at')
    require(isinstance(created,str) and len(created)<=40,'Source creation time is missing or malformed.')
    try:started=datetime.fromisoformat(created.replace('Z','+00:00'))
    except ValueError:raise Failure('Source creation time is malformed.') from None
    require(started.tzinfo is not None,'Source creation time lacks its timezone.')
    age=((now or datetime.now(timezone.utc))-started).total_seconds()
    require(0<=age<SOURCE_LIFETIME_SECONDS,'Source run is outside its supported recovery lifetime.')
    return SOURCE_LIFETIME_SECONDS-age


def continue_monitor(watch,api,revision):
    require(isinstance(revision,str) and re.fullmatch(r'[a-f0-9]{40}',revision),
            'Recovery continuation revision is invalid.')
    source=startup_read(api,f'repos/{watch.repo}/actions/runs/{watch.run_id}')
    watch.validate(source)
    if source.get('status')=='completed':
        return {'status':source.get('conclusion'),'run_id':watch.run_id,'attempt':source['run_attempt']}
    remaining_lifetime(source)
    # A 204 confirms dispatch acceptance, not successor execution. Exit after
    # acceptance: the same source concurrency group queues the next segment.
    api('POST',f'repos/{watch.repo}/actions/workflows/{RECOVERY_WORKFLOW}/dispatches',
        {'ref':'main','inputs':{'source_run_id':str(watch.run_id),'review_revision':revision}})
    return {'status':'continuation_requested','run_id':watch.run_id,'attempt':source['run_attempt']}


class Watch:
    def __init__(self, repo, repository_id, run_id, workflow, sha):
        require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) and type(repository_id) is int and repository_id>0
                and type(run_id) is int and run_id>0 and re.fullmatch(r'[a-f0-9]{40}',sha)
                and re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml',workflow), 'Invalid recovery identity.')
        self.repo,self.repository_id,self.run_id,self.workflow,self.sha=repo,repository_id,run_id,workflow,sha
        self.queued,self.offline={},{}

    def validate(self, run):
        require(isinstance(run,dict) and run.get('id')==self.run_id
                and run.get('repository',{}).get('id')==self.repository_id
                and run.get('repository',{}).get('full_name')==self.repo
                and run.get('head_repository',{}).get('id')==self.repository_id
                and run.get('head_sha')==self.sha and run.get('head_branch')=='main'
                and run.get('event') in ('push','workflow_dispatch','schedule')
                and run.get('path')==self.workflow and type(run.get('run_attempt')) is int
                and run['run_attempt']>=1, 'Run identity or trusted-main boundary changed; recovery refused.')

    def observe(self, run, jobs, runners, now):
        self.validate(run)
        if run.get('status')=='completed':return 'complete'
        if run['run_attempt']!=1:return 'wait'
        require(isinstance(jobs,list) and isinstance(runners,list), 'Incomplete recovery observation.')
        roster={row['id']:row['status'] for row in runners if isinstance(row,dict) and type(row.get('id')) is int}
        # GitHub can report downstream jobs as queued while dependencies run.
        # Only use queue absence evidence after all running work has finished.
        # Offline running jobs are still independently recoverable below.
        running=any(isinstance(j,dict) and j.get('status')=='in_progress' for j in jobs)
        if running:self.queued.clear()
        current=set()
        for job in jobs:
            require(isinstance(job,dict) and type(job.get('id')) is int and isinstance(job.get('labels'),list), 'Malformed job observation.')
            identity=job['id'];current.add(identity)
            if 'self-hosted' not in job['labels']:continue
            if job.get('status')=='queued' and not running:
                self.queued.setdefault(identity,now)
                if now-self.queued[identity]>=120:return 'recover'
            else:self.queued.pop(identity,None)
            if job.get('status')=='in_progress' and type(job.get('runner_id')) is int and job['runner_id']>0:
                if roster.get(job['runner_id']) in (None,'offline'):
                    self.offline.setdefault(identity,now)
                    if now-self.offline[identity]>=30:return 'recover'
                else:self.offline.pop(identity,None)
            else:self.offline.pop(identity,None)
        self.queued={k:v for k,v in self.queued.items() if k in current}
        self.offline={k:v for k,v in self.offline.items() if k in current}
        return 'wait'


def recover(watch, api, *, sleep=time.sleep, attempts=12):
    root=f'repos/{watch.repo}/actions/runs/{watch.run_id}'
    api('POST',root+'/cancel')
    for _ in range(attempts):
        try:value=api('GET',root)
        except ObservationUnavailable:
            sleep(5)
            continue
        watch.validate(value)
        require(value['run_attempt']==1,'Another attempt already started; no duplicate retry.')
        if value.get('status')=='completed':
            require(value.get('conclusion')=='cancelled','Source finished without recovery cancellation; no retry.')
            api('POST',root+'/rerun')
            return
        sleep(5)
    raise Failure('Cancellation is unconfirmed; no competing attempt was started.')


class GitHub:
    def __init__(self, token):
        require(bool(token),'GitHub authorization is missing.')
        self.token=token

    def __call__(self, method, path, payload=None):
        comparison=method=='GET' and re.fullmatch(r'repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/compare/[a-f0-9]{40}\.\.\.[a-f0-9]{40}',path)
        require(method in ('GET','POST') and re.fullmatch(r'[A-Za-z0-9_.?=&/-]+',path) and ('..' not in path or comparison),
                'Unsupported recovery API request.')
        body=None if payload is None else json.dumps(payload).encode()
        request=urllib.request.Request('https://api.github.com/'+path,data=body,method=method,
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2026-03-10','Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,timeout=30) as response:
                data=response.read(2*1024*1024+1)
            require(len(data)<=2*1024*1024,'Recovery response exceeded its bound.')
            return json.loads(data) if data else {}
        except urllib.error.HTTPError as error:
            kind=ObservationUnavailable if method=='GET' and (error.code==429 or error.code>=500) else Failure
            raise kind(f'GitHub recovery request failed (HTTP {error.code}); response suppressed.') from None
        except (OSError,ValueError):
            kind=ObservationUnavailable if method=='GET' else Failure
            raise kind('GitHub recovery observation unavailable; no conclusion inferred.') from None


def pages(api,path,key):
    result=[];total=None;identities=set()
    for page in range(1,101):
        response=api('GET',path+('&' if '?' in path else '?')+f'per_page=100&page={page}')
        rows=response.get(key)
        require(isinstance(rows,list) and type(response.get('total_count')) is int, 'Incomplete provider inventory.')
        if total is not None and total!=response['total_count']:
            raise ObservationUnavailable('Changing provider inventory; no conclusion inferred.')
        total=response['total_count']
        for row in rows:
            require(isinstance(row,dict) and type(row.get('id')) is int and row['id'] not in identities,'Repeated provider observation.')
            identities.add(row['id'])
        result.extend(rows)
        require(len(result)<=total,'Provider inventory exceeds its declared size.')
        if len(result)==total:return result
        require(bool(rows),'Provider pagination ended early.')
    raise Failure('Provider inventory exceeds its supported bound.')


def monitor(watch, api, roster_api, *, owner, timeout=3600, sleep=time.sleep, clock=time.monotonic,continuation=None):
    root=f'repos/{watch.repo}/actions/runs/{watch.run_id}'
    deadline=clock()+timeout;retried=False
    def observed(target):
        def read(method,path,payload=None):
            require(method=='GET','Recovery observation must be read-only.')
            if clock()>=deadline:raise ObservationUnavailable('Recovery segment observation deadline reached.')
            return target(method,path) if payload is None else target(method,path,payload)
        return read
    read_api,read_roster=observed(api),observed(roster_api)
    while clock()<deadline:
        try:
            value=read_api('GET',root);watch.validate(value)
            if value.get('status')=='completed':
                status=value.get('conclusion')
                if value['run_attempt'] > 1 and status == 'failure':
                    completed=pages(read_api,root+f'/attempts/{value["run_attempt"]}/jobs','jobs')
                    for job in completed:
                        if job.get('steps') or job.get('runner_name'):
                            continue
                        check=job.get('check_run_url','')
                        match=re.fullmatch(r'https://api.github.com/repos/'+re.escape(watch.repo)+r'/check-runs/([1-9][0-9]*)',check)
                        if match:
                            annotations=read_api('GET',f'repos/{watch.repo}/check-runs/{match[1]}/annotations')
                            if isinstance(annotations,list) and any('spending limit' in str(a.get('message','')).lower() or 'account payments have failed' in str(a.get('message','')).lower() for a in annotations):
                                status='quota_or_billing_blocked'
                return {'status':status, 'run_id':watch.run_id,'attempt':value['run_attempt']}
            jobs=pages(read_api,root+f'/attempts/{value["run_attempt"]}/jobs','jobs')
            roster=pages(read_roster,owner,'runners') if value['run_attempt']==1 else []
            decision=watch.observe(value,jobs,roster,clock())
        except ObservationUnavailable:
            # An observation gap is not continuous queue/offline evidence.
            # Retry reads within the same deadline, never POST mutations.
            watch.queued.clear();watch.offline.clear()
            sleep(30)
            continue
        if decision=='recover' and not retried:
            recover(watch,api,sleep=sleep);retried=True
        sleep(30)
    if continuation is not None:return continuation()
    raise Failure('Recovery observation timed out; inspect the linked run. No further retries started.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--event',default=os.environ.get('GITHUB_EVENT_PATH'))
    args=parser.parse_args(argv)
    started=time.monotonic()
    try:
        with open(args.event,encoding='utf-8') as stream:event=json.load(stream)
        with open(args.manifest,encoding='utf-8') as stream:manifest=json.load(stream)
        repo=event['repository'];current_revision=os.environ.get('GITHUB_WORKFLOW_SHA')
        require(os.environ.get('GITHUB_REF')=='refs/heads/main' and repo.get('default_branch')=='main'
                and isinstance(repo.get('full_name'),str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo['full_name']),
                'Recovery must execute its trusted main workflow in the intended repository.')
        api=GitHub(os.environ.get('GH_TOKEN'))
        if os.environ.get('GITHUB_EVENT_NAME')=='workflow_run':
            selected=event['workflow_run']['id'];revision=current_revision
            require(type(selected) is int and selected>0,'Recovery source run identity is invalid.')
        else:
            require(os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch','Unsupported recovery event.')
            inputs=event['inputs']
            require(isinstance(inputs,dict) and set(inputs)=={'source_run_id','review_revision'}
                    and isinstance(inputs['source_run_id'],str) and re.fullmatch(r'[1-9][0-9]{0,19}',inputs['source_run_id']),
                    'Recovery continuation inputs are invalid.')
            selected=int(inputs['source_run_id']);revision=inputs['review_revision']
        reviewed=review_manifest(manifest,api,repo['full_name'],revision,current_revision)
        source=startup_read(api,f'repos/{repo["full_name"]}/actions/runs/{selected}')
        watch=Watch(repo['full_name'],repo['id'],source['id'],source['path'],source['head_sha']);watch.validate(source)
        require(source['id']==selected,'Recovery source run identity changed.')
        agreed=contract(reviewed,manifest,source['path'])
        content=startup_read(api,f'repos/{watch.repo}/contents/{watch.workflow}?ref={watch.sha}')
        require(hashlib.sha256(content_bytes(content)).hexdigest()==agreed['sha256'],
                'Source workflow differs from its reviewed retry-safe contract.')
        roster_api=GitHub(os.environ.get('RUNNER_ROSTER_TOKEN'))
        roster=f'orgs/{repo["owner"]["login"]}/actions/runners' if repo['owner']['type']=='Organization' else f'repos/{watch.repo}/actions/runners'
        remaining=min(SEGMENT_SECONDS-(time.monotonic()-started),remaining_lifetime(source))
        continuation=lambda:continue_monitor(watch,api,revision)
        result=monitor(watch,api,roster_api,owner=roster,timeout=remaining,
            continuation=continuation,clock=time.monotonic,sleep=time.sleep) if remaining>0 else continuation()
        print(json.dumps({'schema_version':1,**result}))
        return 0 if result['status'] in ('success','continuation_requested') else 1
    except (Failure,OSError,ValueError,KeyError,TypeError,StopIteration) as error:
        message=str(error) if isinstance(error,Failure) else 'Recovery input or observation is invalid; no retry authorized.'
        print(json.dumps({'schema_version':1,'status':'blocked','reason':message}))
        return 2


if __name__=='__main__':raise SystemExit(main())
