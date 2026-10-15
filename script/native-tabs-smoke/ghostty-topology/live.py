"""Explicitly authorized real Ghostty native-menu topology test. No manager lifecycle actions."""
import argparse
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-live', action='store_true', help='Explicitly authorize launching disposable Ghostty windows and native menu actions')
parser.add_argument('--expect-existing-window', action='append', required=False, default=[], metavar='ID:WORKSPACE', help='Required preservation baseline; repeat for multiple windows')
parser.add_argument('--test-workspace-prefix', default='native-tab-test-ghostty-topology', help='Dedicated empty workspace pair prefix (must start with native-tab-test-)')
args = parser.parse_args()
if not args.run_live:
    parser.error('Live execution requires --run-live; no application or native process APIs were used.')
if not args.expect_existing_window:
    parser.error('Supply --expect-existing-window ID:WORKSPACE from the current session.')
if (not args.test_workspace_prefix.startswith('native-tab-test-')
        or len(args.test_workspace_prefix) > 96
        or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.test_workspace_prefix)):
    parser.error('Use a dedicated lowercase native-tab-test- workspace prefix.')
expectations = []
for expectation in args.expect_existing_window:
    window, separator, workspace = expectation.partition(':')
    if not separator or not window.isdigit() or not workspace:
        parser.error('Expected ID:WORKSPACE with a numeric window ID and nonempty workspace.')
    expectations.append((window, workspace))

import json, subprocess, threading, time, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'script'))
import aquaspace_update as u
import focus_restore
from native_menu import NativeAX, Request as MenuRequest, canonical_membership, run_menu
from script_driver import ScriptDriver
SOURCE=Path(__file__).resolve().parent
HERE=ROOT/'.debug/aquaspace/ghostty-topology'
for helper in ('script-driver.dylib', 'launch-ghostty'):
 if not (HERE/helper).is_file():parser.error('Build the diagnostic helpers first; see '+str(SOURCE/'README.md'))
HERE.mkdir(parents=True,exist_ok=True)
OUT=HERE/('run-'+time.strftime('%Y%m%d-%H%M%S'));OUT.mkdir();(HERE/'current').write_text(str(OUT))
N=u.NativeSystem();CLI=Path('/Applications/AquaSpace.app/Contents/MacOS/aquaspace-cli')
A=args.test_workspace_prefix+'-a';B=args.test_workspace_prefix+'-b'
prefix='AeroSpace-NativeTabs-'+uuid.uuid4().hex[:12]+'-'
stop=threading.Event();errors=[];checks=[];created={};pid=None;birth=None;manager=None;baseline={};samples=[];wsamples=[]
focus_controller=None;focus_ax=None;activity=None
def save(name,v):u.atomic_json(OUT/name,v)
def event(kind,**v):
 with (OUT/'events.jsonl').open('a') as f:f.write(json.dumps(dict(at=time.time_ns(),kind=kind,**v))+'\n')
def check(ok,note,**v):
 if not ok:raise AssertionError(note+': '+str(v))
 checks.append(dict(note=note,**v));print('PASS: '+note,flush=True)
def cli(*args):return N.run([CLI,*args],timeout=5)
def ownership():
 if errors:raise RuntimeError(str(errors))
 peers=N.managers()
 if len(peers)!=1 or peers[0].executable!=Path('/Applications/AquaSpace.app/Contents/MacOS/AquaSpace') or (manager and peers[0].identity!=manager.identity):raise RuntimeError('Manager identity/count changed')
 status=json.loads(cli('management-status'))
 if not u.valid_status(status,peers[0]) or not status.get('inventoryVerified'):raise RuntimeError('Manager ownership unverified')
 return peers[0],status

def rows():return {r[0]:r for r in (line.split('\t') for line in cli('list-windows','--all','--format','%{window-id}\t%{workspace}\t%{window-layout}\t%{window-is-fullscreen}').splitlines())}
def diff(now):return {w:dict(before=r,after=now.get(w)) for w,r in baseline.items() if w not in now or (now[w][1],now[w][3])!=(r[1],r[3])}
def preserved():
 ownership();wrong=diff(rows())
 if wrong:raise RuntimeError('Original window changed: '+str(wrong))
def monitor_process():
 try:
  reader=u.DarwinProcesses()
  with (OUT/'process-samples.jsonl').open('w') as f:
   while not stop.is_set():
    peers=reader.managers();r=dict(at=time.time_ns(),managers=[p.record() for p in peers]);samples.append(r);f.write(json.dumps(r)+'\n');f.flush()
    if len(peers)!=1 or peers[0].identity!=manager.identity:raise RuntimeError('Manager count/identity changed')
    stop.wait(.025)
 except BaseException as e:errors.append(repr(e))
def monitor_windows():
 try:
  with (OUT/'window-samples.jsonl').open('w') as f:
   while not stop.is_set():
    now=rows();wrong=diff(now);r=dict(at=time.time_ns(),original={w:now.get(w) for w in baseline},wrong=wrong);wsamples.append(r);f.write(json.dumps(r)+'\n');f.flush()
    if wrong:raise RuntimeError('Original window changed: '+str(wrong))
    stop.wait(.2)
 except BaseException as e:errors.append(repr(e))
def ghost_birth(target):
 info=N.processes._info(target);path=N.processes._executable(target)
 if info is None or path!=Path('/Applications/Ghostty.app/Contents/MacOS/ghostty'):raise RuntimeError('Ghostty process identity unavailable')
 return (info.pbi_pid,info.pbi_start_tvsec,info.pbi_start_tvusec,info.pbi_uid)
def isolated():
 preserved()
 if pid in protected or ghost_birth(pid)!=birth:raise RuntimeError('Disposable process identity changed')
def focus_guard(phase):
 try:
  isolated()
  return activity.check(phase)
 except Exception as error:
  focus_controller.revoke(error)
  raise
def destinations_guard():
 try:
  focus_guard('before-destination-validation');membership=native();owned=own_rows()
  inventories={w:cli('list-windows','--workspace',w,'--format','%{window-id}\t%{workspace}\t%{app-pid}\t%{window-title}') for w in (A,B)}
  focus_restore.require_owned_destinations(inventories,owned,membership,pid,prefix,set(baseline))
 except Exception as error:
  focus_controller.revoke(error)
  raise
def driver(op,*args,personal=False):
 if not personal:isolated()
 target=personal if personal else pid
 snapshot=None
 if op in ('create','add','select','activate'):
  if personal:raise RuntimeError('No personal-process mutations')
  destinations_guard()
  snapshot=focus_guard('before-script-'+op)
 proof={} if personal else dict(expected_birth=birth,protected_pids=protected)
 if op=='status' and not personal:op='owned-status'
 try:v=script_driver.run(target,op,*args,source=snapshot,confirm_source=focus_controller.confirm_native if snapshot is not None else None,**proof)
 except Exception as error:
  if snapshot is not None:focus_controller.revoke(error)
  raise
 event('script',pid=target,operation=op,result=v);return v

def own_rows():
 text=cli('list-windows','--monitor','all','--app-bundle-id','com.mitchellh.ghostty','--format','%{window-id}\t%{workspace}\t%{app-pid}\t%{window-title}')
 result=[line.split('\t',3) for line in text.splitlines()]
 result=[r for r in result if r[2]==str(pid)]
 if any(not r[3].startswith(prefix) or r[0] in baseline for r in result):raise RuntimeError('Unowned test process window')
 return result

def native():
 try:
  v=driver('status')
  if not isinstance(v,dict) or not isinstance(v.get('groups'),list):raise RuntimeError('Unreadable native tab membership')
  if v['groups']:canonical_membership(v,prefix)
  members=[m for g in v['groups'] for m in g['members']]
  expected={(c['tab'],c['terminal'],c['title']) for c in created.values()}
  if len(members)!=len(expected) or {(m['tab'],m['terminal'],m['title']) for m in members}!=expected:raise RuntimeError('Unexpected native tab ownership: '+str(v))
  return v
 except Exception as error:
  if focus_controller is not None:focus_controller.revoke(error)
  raise

def require_restoration_source(source):
 # Prefix-only CLI rows never grant a focus write. Bind the observed source to
 # fresh proof-owned native membership and exact selected model rows instead.
 try:
  isolated();membership=native();owned=own_rows()
  inventories={w:cli('list-windows','--workspace',w,'--format','%{window-id}\t%{workspace}\t%{app-pid}\t%{window-title}') for w in (A,B)}
  focus_restore.require_owned_destinations(inventories,owned,membership,pid,prefix,set(baseline))
  m=source.model
  matches=[r for r in owned if r[0]==m['window']]
  if (len(matches)!=1 or not m['unique'] or source.pid!=pid or source.window is None or
      m['pid']!=pid or m['title']!=source.title or matches[0][1:]!=[m['workspace'],str(pid),source.title]):
   raise focus_restore.FocusError('Restoration source is not an exact recorded selected window')
  return True
 except Exception as error:
  focus_controller.revoke(error)
  raise

def settle(groups,note):
 # Expected partition maps label sets to workspace. Native group selection is read independently.
 end=time.monotonic()+8;stable=0
 while time.monotonic()<end:
  preserved();v=native();focus_guard('topology-poll');actual={};partition={};reverse={c['tab']:label for label,c in created.items()}
  for g in v['groups']:
   labels=frozenset(reverse[m['tab']] for m in g['members']);partition[labels]=g
  rr=own_rows();actual={r[3][len(prefix):]:r[1] for r in rr}
  expected={reverse[g['selected']]:groups[labels] for labels,g in partition.items() if labels in groups}
  ok=set(partition)==set(groups) and len(rr)==len(groups) and actual==expected
  stable=stable+1 if ok else 0
  event('topology',note=note,native=v,rows=rr,expected=[dict(labels=sorted(k),workspace=w) for k,w in groups.items()])
  if stable>=3:
   check(True,note,native=v,rows=rr);return {r[3][len(prefix):]:r[0] for r in rr}
  time.sleep(.12)
 raise AssertionError(note+': '+str(dict(actual=actual,expected=expected,native=v)))
def focus_test_workspace(workspace):
 if workspace not in (A,B):raise RuntimeError('Creation requires a dedicated test workspace')
 isolated()
 destinations_guard()
 source=focus_guard('before-test-workspace')
 try:
  cli('workspace',workspace)
  if cli('list-workspaces','--focused')!=workspace:raise RuntimeError('Test workspace did not become focused; refusing creation')
  activity.workspace_selected(workspace,source)
  preserved()
  focus_guard('after-test-workspace')
 except Exception as error:
  focus_controller.revoke(error)
  raise
def fresh(label,anchor=None,workspace=A):
 focus_test_workspace(workspace)
 if anchor:v=driver('add',prefix+label,created[anchor]['tab'],created[anchor]['terminal'])
 else:v=driver('create',prefix+label)
 created[label]=v;save('created.json',created);preserved();return v

def activate_initial(label):
 # Creation and CLI focus can leave another instance of the same bundle native
 # foreground. Make the initial, explicitly guarded handoff before leaving A.
 native();source=focus_guard('before-initial-activation')
 if source.pid==pid:return
 try:
  c=created[label];driver('activate',c['tab'],c['terminal'])
  end=time.monotonic()+5
  while time.monotonic()<end:
   if focus_guard('initial-activation-poll').pid==pid:return
   time.sleep(.1)
  raise focus_restore.FocusError('Initial native activation did not complete; no retry')
 except Exception as error:
  focus_controller.revoke(error)
  raise

def select(label):
 native();c=created[label];driver('select',c['tab'],c['terminal']);time.sleep(.2);preserved()
 rr=[r for r in own_rows() if r[3]==prefix+label]
 if len(rr)!=1:raise RuntimeError('Selected test window not uniquely identified')
 destinations_guard()
 focus_guard('before-selected-window-focus')
 try:cli('focus','--window-id',rr[0][0]);preserved()
 except Exception as error:
  focus_controller.revoke(error)
  raise
def move(label,workspace):
 isolated();native();rr=own_rows();matches=[r for r in rr if r[3]==prefix+label]
 if len(matches)!=1:raise RuntimeError('No unique selected disposable window')
 if workspace not in (A,B):raise RuntimeError('Moves require a dedicated test workspace')
 destinations_guard()
 focus_guard('before-test-window-move')
 try:cli('move-node-to-workspace','--window-id',matches[0][0],'--focus-follows-window',workspace);preserved()
 except Exception as error:
  focus_controller.revoke(error)
  raise
def menu(op,label):
 select(label);v=native();c=created[label]
 if not any(g['selected']==c['tab'] for g in v['groups']):raise RuntimeError('Requested menu selection not active')
 isolated();destinations_guard();focus_guard('before-native-menu')
 backend=None
 def read_membership():
  current=native();focus_guard('native-menu-membership');return current
 try:
  backend=NativeAX()
  result=run_menu(MenuRequest(op,pid,protected[0],prefix,prefix+label),backend,expected=v,read_membership=read_membership)
 except Exception as error:
  focus_controller.revoke(error)
  raise
 finally:
  if backend is not None:backend.close()
 event('native-menu',operation=op,label=label,result=result);preserved()
def G(labels,ws):return (frozenset(labels),ws)
def personal_identity(v):return sorted((g['selected'],tuple(sorted((m['tab'],m['terminal']) for m in g['members']))) for g in v['groups'])
def record_cleanup_focus(phase):
 if focus_controller is not None:
  try:focus_controller.observe(phase)
  except Exception as error:event('focus-observation-error',phase=phase,error=repr(error))

def require_background_cleanup(phase):
 # A restoration failure can leave our own app foreground. Observations for
 # admission must fail closed, unlike best-effort diagnostic observations.
 current=focus_controller.observe(phase)
 if current.pid==pid or current.model['pid']==pid:
  raise focus_restore.FocusError('Disposable Ghostty is not confirmed background; leaving cleanup incomplete')

script_driver=ScriptDriver(HERE/'script-driver.dylib')
manager,status=ownership();session=N.capture_session(manager,CLI);baseline={r[0]:r for r in session['windows']}
save('baseline-app-identities.json',cli('list-windows','--all','--format','%{window-id}\t%{workspace}\t%{app-name}\t%{app-pid}').splitlines())
originals=json.loads(N.run([HERE/'launch-ghostty','list'],timeout=5));protected=[int(r['pid']) for r in originals]
check(len(protected)==1,'one existing Ghostty process identified')
check(ghost_birth(protected[0]) is not None,'existing Ghostty process identified by kernel executable/birth')
personal=driver('status',personal=protected[0]);personal_before=personal_identity(personal)
for window,workspace in expectations:
 check(baseline.get(window,[None,None])[1]==workspace,'expected existing window is on its original workspace',window=window,workspace=workspace)
check(not cli('list-windows','--all','--format','%{workspace}').splitlines().count(A) and not cli('list-windows','--all','--format','%{workspace}').splitlines().count(B),'dedicated test workspaces are empty')
focus_ax=NativeAX(frontmost_pid=lambda:int(N.run([HERE/'launch-ghostty','frontmost-pid'],timeout=5)))
focus_controller=focus_restore.FocusRestoration(
 ax=focus_ax,read_model=lambda:focus_restore.read_model(cli),cli=cli,guard=preserved,
 log=lambda phase,data:event('desktop-focus',**data))
try:focus_controller.capture_initial(session)
except BaseException as error:
 try:save('focus-capture-failure.json',dict(error=repr(error),session=session,focus_restoration=focus_controller.report()))
 finally:focus_ax.close()
 raise
activity=focus_restore.ExecutionFocus(focus_controller,test_pid=lambda:pid,owned_rows=own_rows,workspaces={A,B},prefix=prefix)
save('baseline.json',dict(manager=manager.record(),status=status,session=session,personal=personal,ghostty_processes=originals,prefix=prefix,desktop_focus=focus_controller.report()))
threads=[threading.Thread(target=monitor_process,daemon=True),threading.Thread(target=monitor_windows,daemon=True)]
for t in threads:t.start()
success=False
try:
 config=OUT/'config';config.write_text('initial-window = false\nwindow-save-state = never\nquit-after-last-window-closed = false\ncommand = /bin/cat\nconfirm-close-surface = false\nmacos-applescript = true\ntitle = '+prefix+'initial\n')
 isolated_before=set(protected);preserved();activity.check('before-disposable-launch')
 pid=int(N.run([HERE/'launch-ghostty','launch',config],timeout=20));birth=ghost_birth(pid)
 save('isolated-process.json',dict(pid=pid,birth=birth,protected=protected))
 check(pid not in isolated_before,'separate disposable installed Ghostty instance launched')
 time.sleep(.8);check(driver('status')['groups']==[],'disposable instance restores no existing windows')
 fresh('a')
 end=time.monotonic()+6
 while not own_rows():
  preserved();focus_guard('initial-window-poll')
  if time.monotonic()>end:raise RuntimeError('New test window not observed')
  time.sleep(.1)
 activate_initial('a')
 move('a',A);settle(dict([G('a',A)]),'standalone anchored on test workspace A')
 fresh('b','a');settle(dict([G('ab',A)]),'two native Ghostty tabs share one workspace slot')
 move('b',B);settle(dict([G('ab',B)]),'native tab group moved to test workspace B')
 for label in 'ab':select(label);settle(dict([G('ab',B)]),'selection '+label+' retains group workspace B')
 fresh('c');move('c',A);settle(dict([G('ab',B),G('c',A)]),'independent destination window anchored on test workspace A')
 menu('merge','c');settle(dict([G('abc',A)]),'native Merge All Windows joins groups onto destination A')
 for label in 'abc':select(label);settle(dict([G('abc',A)]),'merged selection '+label+' retains destination A')
 menu('detach','c');settle(dict([G('ab',A),G('c',A)]),'native detach from three tabs preserves source workspace A')
 move('c',B);settle(dict([G('ab',A),G('c',B)]),'detached tab moves independently to test workspace B')
 menu('detach','b');settle(dict([G('a',A),G('b',A),G('c',B)]),'native two-to-one detach leaves independent bare windows')
 menu('merge','c');settle(dict([G('abc',B)]),'native merge of three bare windows uses destination B')
 for label in 'bac':select(label);settle(dict([G('abc',B)]),'remerged selection '+label+' retains destination B')
 menu('detach','a');settle(dict([G('a',B),G('bc',B)]),'second detach preserves remerged destination B')
 move('a',A);settle(dict([G('a',A),G('bc',B)]),'second detached tab moves independently to A')
 menu('merge','a');settle(dict([G('abc',A)]),'native remerge targets moved destination A')
 check(personal_identity(driver('status',personal=protected[0]))==personal_before,'existing Ghostty native tab and terminal identities unchanged')
 preserved();success=True
except BaseException as e:
 save('failure.json',dict(error=repr(e),errors=errors,checks=checks));print('STOPPED: '+repr(e),flush=True);raise
finally:
 # Restore while our last window still owns actual foreground focus. Closing a
 # foreground app first can hand focus to an unrelated window/workspace.
 try:
  focus_controller.restore_before_cleanup(pid,require_restoration_source,{A,B},prefix)
 except Exception as error:
  errors.append('Focus restoration: '+repr(error));success=False
  event('focus-restoration-error',error=repr(error))
 if pid is not None and birth is not None:
  try:
   if ghost_birth(pid)==birth and pid not in protected:
    # Only exact created tab/terminal pairs can be closed, even after a failed check.
    for c in reversed(list(created.values())):
     peers=N.managers()
     if len(peers)!=1 or peers[0].identity!=manager.identity:raise RuntimeError('No cleanup mutation with uncertain manager ownership')
     record_cleanup_focus('before-close-'+c['tab'])
     if ghost_birth(pid)!=birth:raise RuntimeError('Process changed before close')
     require_background_cleanup('admit-close-'+c['tab'])
     event('cleanup',result=script_driver.run(pid,'close',c['tab'],c['terminal'],expected_birth=birth,protected_pids=protected))
     record_cleanup_focus('after-close-'+c['tab'])
    remaining=script_driver.run(pid,'status')
    if remaining['groups']:raise RuntimeError('Unowned remaining tabs; leaving process alone')
    if ghost_birth(pid)!=birth:raise RuntimeError('Process changed before termination')
    record_cleanup_focus('before-terminate')
    # Focus observations can block. Revalidate afterwards, then let the native
    # receiver independently bind the expected process and require it empty.
    preserved()
    if ghost_birth(pid)!=birth:raise RuntimeError('Process changed before termination')
    remaining=script_driver.run(pid,'status')
    if remaining['groups']:raise RuntimeError('Unowned remaining tabs; leaving process alone')
    require_background_cleanup('admit-terminate')
    event('cleanup-terminate',result=script_driver.run(pid,'terminate',expected_birth=birth,protected_pids=protected))
    end=time.monotonic()+5
    while time.monotonic()<end:
     if N.processes._info(pid) is None:break
     time.sleep(.1)
    check(N.processes._info(pid) is None,'only disposable Ghostty process exited')
    record_cleanup_focus('after-terminate')
  except BaseException as e:
   errors.append('Cleanup: '+repr(e));success=False
   event('cleanup-incomplete',pid=pid,recorded_tabs=list(created.values()),error=repr(e))
 try:
  preserved();check(not own_rows(),'no disposable managed windows remain')
  check(personal_identity(driver('status',personal=protected[0]))==personal_before,'existing Ghostty native identities preserved after cleanup')
  check(not diff(rows()),'every original window workspace and fullscreen state preserved')
  check(focus_controller.verify_after_cleanup(),'original workspace, model window and native foreground focus stably restored')
  preserved()
 except BaseException as e:errors.append('Final verification: '+repr(e));success=False
 stop.set()
 for t in threads:t.join(timeout=6)
 focus_result=focus_controller.report()
 focus_ax.close()
 success=success and focus_result['verified']
 save('results.json',dict(success=success and not errors,errors=errors,checks=checks,manager=manager.record(),manager_samples=len(samples),peak_managers=max((len(s['managers']) for s in samples),default=0),window_samples=len(wsamples),final_windows=rows(),focus_restoration=focus_result))
 print('RESULT: '+json.dumps(dict(success=success and not errors,assertions=len(checks),manager_samples=len(samples),window_samples=len(wsamples),errors=errors,output=str(OUT))),flush=True)
 if errors or not success:sys.exit(2)
