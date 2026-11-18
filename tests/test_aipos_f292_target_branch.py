"""F292: temporary real repositories, official claim/return binding and merge paths."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_aipos_f184_queue_landing_supersede import rig, _card, _git, EXEC, AUDITOR, DRIVER
from test_aipos_f285_concurrent_landing_claim_residue import _claim, _loc
from test_aipos_f73d_loop_driver import _fm, _write
from tools.aipos_cli.frontmatter import require_frontmatter
from tools.aipos_cli.target_branch import card_target_branch, target_branch_problem
from tools.aipos_cli.next_resolver import card_expected_base, return_binding_subject, card_delivery_subject, _ensure_worktree
from tools.aipos_cli.board_adapter import _card_branch_changed_files, _check_branch_compliance
from tools.aipos_cli.clock import iso_z

TASK = 'PROJ-292'


def _scenario(rig):
    gov = rig['gov']
    repo = Path(json.loads((gov/'project.json').read_text())['code_repo'])
    _git(repo, 'config', 'user.name', 'fixture')
    _git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo/'tools').mkdir()
    _write(repo/'tools/base.txt', 'base\n')
    _git(repo, 'add', '--', 'tools/base.txt')
    _git(repo, 'commit', '-qm', 'shared baseline')
    _git(repo, 'branch', 'ota')
    _write(repo/'tools/main_only.txt', 'must not leak\n')
    _git(repo, 'add', '--', 'tools/main_only.txt')
    _git(repo, 'commit', '-qm', 'unrelated main change')
    project = json.loads((gov/'project.json').read_text())
    project['repos'] = {'default':'mobile', 'items':{'mobile':str(repo)}, 'no_deploy':['mobile']}
    (gov/'project.json').write_text(json.dumps(project))
    _card(gov,TASK,'pending',extra={'target_branch':'ota','lane':{'repo':'mobile','paths':['tools/']}})
    return gov, repo, _git(repo,'rev-parse','main')


def _claimed(rig):
    gov,repo,main = _scenario(rig)
    result = _claim(gov,TASK)
    assert result.get('ok', result.get('verdict') != 'BLOCK'), result
    from tools.aipos_cli.task_loader import find_task_card
    path,_ = find_task_card(gov,TASK)
    fm,_ = require_frontmatter(path)
    assert fm['claimed_target_branch'] == 'ota', result
    _,wt = _loc(gov,TASK)
    assert _git(wt,'rev-parse','HEAD') == _git(repo,'rev-parse','ota')
    assert not (wt/'tools/main_only.txt').exists()
    _write(wt/'tools/feature.txt','ota feature\n')
    _git(wt,'add','--','tools/feature.txt')
    _git(wt,'commit','-qm','OTA feature')
    return gov,repo,main,path,fm,wt


def test_claim_return_audit_merge_isolated_from_main(rig,monkeypatch):
    gov,repo,main,path,fm,wt = _claimed(rig)
    assert target_branch_problem(gov,fm,require_binding=True) is None
    assert _check_branch_compliance(task_id=TASK,task_metadata=fm,repo_root=gov) == []
    assert _card_branch_changed_files(repo,TASK,card_frontmatter=fm) == ['tools/feature.txt']
    subject = return_binding_subject(gov,TASK,fm)
    assert subject['target_branch'] == 'ota'
    assert subject['base_commit'] == _git(repo,'rev-parse','ota')
    retid = 'return_PROJ-292_fixture'
    _write(gov/'5_tasks/records/returns'/TASK/(retid+'.md'),_fm({'record_type':'return_record','task_id':TASK,'return_id':retid,
        'artifact_subject':subject}))
    from tools.aipos_cli.audit_derivation import build_derived_audit_task
    audit = build_derived_audit_task(source_task_id=TASK,source_metadata=fm,source_path=str(path.relative_to(gov)),
        return_record_ref=retid,artifact_refs=['task_cards/'+TASK+'/RETURN.md'],repo_root=gov)
    afm=audit['metadata'];assert afm['target_branch']=='ota'
    from tools.aipos_cli.audit_derivation import build_forensic_anchor_section
    section=build_forensic_anchor_section(TASK,gov,fm,audit_task_id=afm['task_id'],audit_metadata=afm)
    assert 'git diff ota...' in section and '--sha '+subject['base_commit'] in section
    from tools.aipos_cli.subagent_kickoff import baseline_state, kickoff_contract
    baseline=baseline_state(gov,repo,kickoff_contract(),'ota')
    assert baseline['baseline_sha']==subject['base_commit']
    assert '--sha '+subject['base_commit'] in baseline['baseline_command']
    from tools.aipos_cli.next_resolver import _build_finalize_command
    command=_build_finalize_command(task_id=TASK,actor=EXEC,code_repo_root=repo,governance_root=gov)
    assert '--deploy' not in command
    _card(gov,afm['task_id'],'pending',assigned=AUDITOR,extra=afm)
    assert card_delivery_subject(gov,afm['task_id'],afm)['base']=='ota'
    detached=_ensure_worktree(gov,afm['task_id'],afm)
    assert detached['ok'],detached
    assert _git(Path(detached['worktree_path']),'rev-parse','HEAD') == subject['commit_sha']
    verdict={'record_type':'audit_verdict_record','verdict_id':'verdict_PROJ-292_fixture','verdict_at':iso_z(),
        'reviewed_task_id':TASK,'verdict':'PASS','reviewed_return_record_ref':retid,'artifact_subject':subject}
    _write(gov/'5_tasks/records/audit_verdicts'/TASK/'verdict_fixture.md',_fm(verdict))
    from tools.aipos_cli import finalize as F
    from tools.aipos_cli.deployment_authorization import find_gate_pass_verdict_for_task
    assert find_gate_pass_verdict_for_task(TASK,gov,subject['commit_sha'])['found']
    monkeypatch.setattr(F,'check_stage_archive_gate',lambda *a,**k:{'passed':True,'message':'fixture stage archive'})
    monkeypatch.setattr(F,'_check_deployment_integrity',lambda *a,**k:{'integrity_ok':True,'current_commit':'x','head_commit':'x',
        'provenance':None,'missing_commits':[],'message':'fixture has no deployment'})
    first=F.finalize_task(task_id=TASK,actor=EXEC,workspace_root=repo,governance_root=gov,dry_run=False,push=False,deploy=False)
    assert first['verdict']=='PASS',first
    ota=_git(repo,'rev-parse','ota')
    assert _git(repo,'rev-parse','main')==main
    assert subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',subject['commit_sha'],'ota']).returncode==0
    assert 'tools/main_only.txt' not in _git(repo,'ls-tree','-r','--name-only','ota').splitlines()
    again=F.finalize_task(task_id=TASK,actor=EXEC,workspace_root=repo,governance_root=gov,dry_run=False,push=False,deploy=False)
    assert again['verdict']=='PASS',again
    assert _git(repo,'rev-parse','ota')==ota and _git(repo,'rev-parse','main')==main


def test_explicit_target_claim_cannot_change_or_reuse_old_return(rig):
    gov,repo,main,path,fm,wt=_claimed(rig)
    changed={**fm,'target_branch':'main'}
    assert 'CHANGED_AFTER_CLAIM' in target_branch_problem(gov,changed,require_binding=True)
    changed['claimed_target_branch']='main'
    assert 'CLAIM_BINDING_MISMATCH' in target_branch_problem(gov,changed,require_binding=True)
    from tools.aipos_cli.target_branch import verdict_target_problem
    assert 'VERDICT_MISMATCH' in verdict_target_problem(gov,TASK,{'artifact_subject':{'commit_sha':_git(wt,'rev-parse','HEAD')}})
    assert _git(repo,'rev-parse','main')==main


@pytest.mark.parametrize('target',['','missing','HEAD','refs/heads/ota','card/PROJ-1','ota~1','../main','-bad','ota '])
def test_bad_target_claim_is_zero_write(rig,target):
    gov,repo,main=_scenario(rig)
    fm={'task_id':TASK,'target_branch':target,'lane':{'repo':'mobile','paths':['tools/']}}
    before=_git(repo,'show-ref')
    out=_ensure_worktree(gov,TASK,fm)
    assert not out['ok'],out
    assert _git(repo,'show-ref')==before and _git(repo,'rev-parse','main')==main
    assert not (repo/'.worktrees'/TASK).exists()


def test_default_and_fix_base_are_separate():
    assert card_target_branch({})=='main'
    assert card_expected_base({'target_branch':'ota','fix_base_branch':'card/PROJ-1'})=='card/PROJ-1'
    assert card_target_branch({'target_branch':'ota','fix_base_branch':'card/PROJ-1'})=='ota'


def test_nondefault_deploy_refused_before_checkout(rig):
    gov,repo,main,path,fm,wt=_claimed(rig)
    from tools.aipos_cli.finalize import finalize_task
    before=_git(repo,'show-ref')
    out=finalize_task(task_id=TASK,actor=EXEC,workspace_root=repo,governance_root=gov,dry_run=False,deploy=True)
    assert out['verdict']=='BLOCK' and 'TARGET_BRANCH_DEPLOY_UNSUPPORTED' in out['message'],out
    assert _git(repo,'show-ref')==before and _git(repo,'symbolic-ref','--short','HEAD')=='main'



def test_nondefault_implicit_deploy_refused_before_checkout(rig, monkeypatch):
    gov,repo,main,path,fm,wt=_claimed(rig)
    from tools.aipos_cli.finalize import finalize_task
    from tools.aipos_cli import deploy_gate
    project=json.loads((gov/'project.json').read_text())
    project['repos']['no_deploy']=[]
    (gov/'project.json').write_text(json.dumps(project))
    # A deployment mechanism really exists; even an omitted --deploy must not reach legacy auto-deploy.
    _write(repo/'tools/lybra-deploy','#!/bin/sh\nexit 99\n')
    _git(repo,'add','--','tools/lybra-deploy')
    _git(repo,'commit','-qm','fixture default deployment mechanism')
    assert deploy_gate.deploy_mechanism_present(repo,gov)['applicable']
    monkeypatch.setattr(deploy_gate,'invoke_lybra_deploy',lambda *a,**k:pytest.fail('non-default deployment invoked'))
    before=_git(repo,'show-ref')
    out=finalize_task(task_id=TASK,actor=EXEC,workspace_root=repo,governance_root=gov,dry_run=False,deploy=False)
    assert out['verdict']=='BLOCK' and 'TARGET_BRANCH_DEPLOY_UNSUPPORTED' in out['message'],out
    assert _git(repo,'show-ref')==before and _git(repo,'symbolic-ref','--short','HEAD')=='main'


def test_finalize_invalid_target_is_explicit_zero_write_block(rig):
    gov,repo,main,path,fm,wt=_claimed(rig)
    from tools.aipos_cli.finalize import finalize_task
    path.write_text(path.read_text().replace('target_branch: ota','target_branch: HEAD'))
    before=_git(repo,'show-ref')
    out=finalize_task(task_id=TASK,actor=EXEC,workspace_root=repo,governance_root=gov,dry_run=False,deploy=False)
    assert out['verdict']=='BLOCK' and 'TARGET_BRANCH_INVALID' in out['message'],out
    assert _git(repo,'show-ref')==before


def test_init_plan_preflight_and_owner_record_preview(rig):
    gov,repo,main=_scenario(rig)
    from tools.aipos_cli.branch_initialization import branch_plan
    from tools.aipos_cli.board_adapter import record_owner_decision
    block={'repo':'mobile','branch':'release-ota','start_commit':main,'requested_at':iso_z()}
    before=_git(repo,'show-ref')
    preview=record_owner_decision({'decision_id':'init-ota-fixture','branch_initialization':block},repo_root=gov,actor='owner')
    assert preview['ok'] and preview['verdict']!='BLOCK',preview
    assert preview['data']['branch_initialization']
    assert _git(repo,'show-ref')==before
    assert branch_plan(gov,{**block,'branch':'main'})['action']=='unchanged'
    with pytest.raises(ValueError,match='EXISTS_DIFFERENT'):
        branch_plan(gov,{**block,'branch':'ota'})
    with pytest.raises(ValueError):
        branch_plan(gov,{**block,'start_commit':'main'})
    with pytest.raises(ValueError):
        branch_plan(gov,{**block,'repo':'foreign'})


def test_init_owner_gate_authentication_and_exact_creation(rig,monkeypatch):
    import threading
    from tools.mcp_server.http_sse import HttpSseConfig, build_http_server
    from tools.aipos_cli.confirm_client import GateClient
    from tools.mcp_server.tests.test_autonomy_preauth_envelope import _registry
    gov,repo,main=_scenario(rig)
    monkeypatch.setenv('AIPOS_WORKSPACE_ROOT',str(gov))
    registry=_registry()
    # Has decision-record scope, but deliberately lacks the Owner-only owner_confirm scope.
    registry['advisor-fixture']={'role':'advisor','token_ref':'advisor-fixture','scopes':['owner_decision_record'],
        'expires_at':'2999-01-01T00:00:00Z','fingerprint':'sha256:advisor292'}
    from tools.mcp_server.http_sse import _token_fingerprint
    registry={_token_fingerprint(token):entry for token,entry in registry.items()}
    server=build_http_server(HttpSseConfig(host='127.0.0.1',port=0,token='',keepalive_seconds=.01,
        max_keepalive_events=1,service_role_registry=registry))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://%s:%s'%server.server_address
    payload={'decision_id':'branch-init-owner-fixture','branch_initialization':{'repo':'mobile','branch':'release-ota',
        'start_commit':main,'requested_at':iso_z()},'actor':'owner','workspace_root':str(gov)}
    before=_git(repo,'show-ref')
    try:
        advisor=GateClient(base,'advisor-fixture');advisor.initialize()
        dry=advisor.call_tool('lybra_owner_decision_record_dry_run',payload)
        assert dry.get('dry_run_token'),dry
        denied=advisor.call_tool('lybra_owner_decision_record_confirm',{'dry_run_token':dry['dry_run_token'],
            'actor':'owner','owner_confirmation_token':'OWNER_CONFIRMED','workspace_root':str(gov)})
        assert not denied.get('ok'),denied
        assert _git(repo,'show-ref')==before
        owner=GateClient(base,'owner-secret');owner.initialize()
        dry=owner.call_tool('lybra_owner_decision_record_dry_run',payload)
        assert dry.get('dry_run_token'),dry
        assert _git(repo,'show-ref')==before
        done=owner.call_tool('lybra_owner_decision_record_confirm',{'dry_run_token':dry['dry_run_token'],
            'actor':'owner','owner_confirmation_token':'OWNER_CONFIRMED','workspace_root':str(gov)})
        assert done.get('ok'),done
        assert _git(repo,'rev-parse','release-ota')==main
        assert _git(repo,'rev-parse','main')==main
        assert done['data']['branch_created'] is True
        record=gov/done['data']['target_path']
        assert record.is_file() and main in record.read_text()
        # A fresh exact request is a safe no-op at the ref layer; different SHA is refused before any record/ref write.
        second={**payload,'decision_id':'branch-init-again'}
        dry2=owner.call_tool('lybra_owner_decision_record_dry_run',second)
        assert dry2.get('dry_run_token'),dry2
        done2=owner.call_tool('lybra_owner_decision_record_confirm',{'dry_run_token':dry2['dry_run_token'],'actor':'owner',
            'owner_confirmation_token':'OWNER_CONFIRMED','workspace_root':str(gov)})
        assert done2['ok'] and not done2['data']['branch_created'],done2
        assert _git(repo,'rev-parse','release-ota')==main
    finally:
        server.shutdown();thread.join(timeout=2);server.server_close()
