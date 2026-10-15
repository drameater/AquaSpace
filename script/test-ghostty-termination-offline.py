#!/usr/bin/env python3
"""Exercise the actual termination guard against fake process and Ghostty APIs.

Only the isolated test binary is built/run. No diagnostic helper/library, native
process reader, Ghostty scripting event, app activation or termination is used.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.debug/ghostty-termination-offline'
source = (ROOT / 'script/native-tabs-smoke/ghostty-topology/ScriptDriver.m').read_text()
guard = source[source.index('// Termination is a background-only operation.'):
               source.index('static NSDictionary *performOperation')]
prelude = r'''
#import <Foundation/Foundation.h>
#import <libproc.h>
#import <sys/proc_info.h>
#import <limits.h>
static uint64_t liveBirth;
static uid_t liveUid;
static int infoReads, pathReads, windowReads, bindings, terminations;
static BOOL shortInfo, deniedPath, wrongPath, zombie, foreignBundle, badNativePath, nativeTerminated;
static BOOL unownedWindow, unreadableWindows, malformedWindows, refuseTermination;
static int changeOnWindow, unownedOnWindow, changeOnInfo;
static BOOL changeOnPath, changeOnBind;
@interface FakeRunningApplication : NSObject
@property NSString *bundleIdentifier;
@property NSURL *executableURL;
@property BOOL terminated;
+ (instancetype)runningApplicationWithProcessIdentifier:(pid_t)pid;
- (BOOL)terminate;
@end
@implementation FakeRunningApplication
+ (instancetype)runningApplicationWithProcessIdentifier:(pid_t)pid {
    bindings++;
    if(changeOnBind)liveBirth++;
    FakeRunningApplication *app=[FakeRunningApplication new];
    app.bundleIdentifier=foreignBundle?@"foreign.app":@"com.mitchellh.ghostty";
    app.executableURL=[NSURL fileURLWithPath:badNativePath?@"/foreign":@"/Applications/Ghostty.app/Contents/MacOS/ghostty"];
    app.terminated=nativeTerminated;return app;
}
- (BOOL)terminate { terminations++;return !refuseTermination; }
@end
@interface GhosttyApplication : NSObject
@property NSInteger timeout;
+ (instancetype)applicationWithProcessIdentifier:(pid_t)pid;
@end
@implementation GhosttyApplication
+ (instancetype)applicationWithProcessIdentifier:(pid_t)pid { return [GhosttyApplication new]; }
@end
static NSArray *windows(GhosttyApplication *app) {
    windowReads++;
    if(changeOnWindow==windowReads)liveBirth++;
    if(unownedOnWindow==windowReads)unownedWindow=YES;
    if(unreadableWindows)return nil;
    if(malformedWindows)return (NSArray *)@"not an array";
    return unownedWindow?@[@"unrecorded-tab"]:@[];
}
static int FakeInfo(int pid,int flavor,uint64_t arg,void *buffer,int size) {
    if(flavor!=PROC_PIDTBSDINFO || size!=sizeof(struct proc_bsdinfo))abort();
    infoReads++;
    if(changeOnInfo==infoReads)liveBirth++;
    if(shortInfo)return 1;
    struct proc_bsdinfo *info=buffer;memset(info,0,sizeof(*info));
    info->pbi_pid=pid;info->pbi_uid=liveUid;info->pbi_status=zombie?5:2;
    info->pbi_start_tvsec=liveBirth;info->pbi_start_tvusec=456;
    return sizeof(*info);
}
static int FakePath(int pid,void *buffer,uint32_t size) {
    pathReads++;if(changeOnPath)liveBirth++;
    if(deniedPath)return 0;
    const char *path=wrongPath?"/foreign":"/Applications/Ghostty.app/Contents/MacOS/ghostty";
    strlcpy(buffer,path,size);return (int)strlen(path);
}
static uid_t FakeUid(void) { return 501; }
#define NSRunningApplication FakeRunningApplication
#define SBApplication GhosttyApplication
#define proc_pidinfo FakeInfo
#define proc_pidpath FakePath
#define getuid FakeUid
'''
main = r'''
static BOOL checkCase(NSString *name,BOOL shouldPass,NSDictionary *(^proof)(void),void(^setup)(void)) {
    liveBirth=123;liveUid=501;infoReads=pathReads=windowReads=bindings=terminations=0;
    shortInfo=deniedPath=wrongPath=zombie=foreignBundle=badNativePath=nativeTerminated=NO;
    unownedWindow=unreadableWindows=malformedWindows=refuseTermination=NO;
    changeOnWindow=unownedOnWindow=changeOnInfo=0;changeOnPath=changeOnBind=NO;
    setup();BOOL passed=NO;
    @autoreleasepool { @try {
        NSDictionary *result=terminateEmptyGhostty(200,proof());
        passed=[result[@"terminationRequested"] isEqual:@YES];
    } @catch(NSException *error) { passed=NO; } }
    // Refusal must not issue any termination, except when testing the API's
    // uncertain/negative reply itself, which must produce exactly one attempt.
    int expected=shouldPass||refuseTermination?1:0;
    if(passed!=shouldPass || terminations!=expected || bindings>1 || windowReads>2) {
        fprintf(stderr,"FAIL: %s (accepted=%d expected=%d terminations=%d)\n",name.UTF8String,passed,shouldPass,terminations);return NO;
    }
    printf("PASS: %s\n",name.UTF8String);return YES;
}
int main(void) { @autoreleasepool {
    NSDictionary *(^valid)(void)=^{return @{@"birth":@[@200,@123,@456,@501],@"protected":@[@100]};};
    BOOL ok=YES;
    ok &= checkCase(@"unchanged empty pinned process terminates once",YES,valid,^{});
    ok &= checkCase(@"unowned tab present at receiver refuses",NO,valid,^{unownedWindow=YES;});
    ok &= checkCase(@"unowned tab appears during receiver recheck",NO,valid,^{unownedOnWindow=2;});
    ok &= checkCase(@"process replaced before receiver binding refuses",NO,valid,^{liveBirth=999;});
    ok &= checkCase(@"process replaced while native app binds refuses",NO,valid,^{changeOnBind=YES;});
    ok &= checkCase(@"process replaced during first membership read refuses",NO,valid,^{changeOnWindow=1;});
    ok &= checkCase(@"process replaced during final membership read refuses",NO,valid,^{changeOnWindow=2;});
    ok &= checkCase(@"process replacement during path read refuses",NO,valid,^{changeOnPath=YES;});
    ok &= checkCase(@"process replacement at final kernel check refuses",NO,valid,^{changeOnInfo=6;});
    ok &= checkCase(@"unreadable native window list is never empty",NO,valid,^{unreadableWindows=YES;});
    ok &= checkCase(@"malformed native window list refuses",NO,valid,^{malformedWindows=YES;});
    ok &= checkCase(@"incomplete kernel identity refuses",NO,valid,^{shortInfo=YES;});
    ok &= checkCase(@"unreadable executable refuses",NO,valid,^{deniedPath=YES;});
    ok &= checkCase(@"changed executable refuses",NO,valid,^{wrongPath=YES;});
    ok &= checkCase(@"changed UID refuses",NO,valid,^{liveUid=502;});
    ok &= checkCase(@"zombie process refuses",NO,valid,^{zombie=YES;});
    ok &= checkCase(@"foreign native bundle refuses",NO,valid,^{foreignBundle=YES;});
    ok &= checkCase(@"foreign native executable refuses",NO,valid,^{badNativePath=YES;});
    ok &= checkCase(@"terminated native app refuses",NO,valid,^{nativeTerminated=YES;});
    ok &= checkCase(@"protected PID refuses",NO,^{return @{@"birth":@[@200,@123,@456,@501],@"protected":@[@200]};},^{});
    ok &= checkCase(@"missing identity refuses",NO,^{return @{};},^{});
    ok &= checkCase(@"boolean identity refuses",NO,^{return @{@"birth":@[@200,@YES,@456,@501],@"protected":@[@100]};},^{});
    ok &= checkCase(@"floating identity refuses",NO,^{return @{@"birth":@[@200,@123.0,@456,@501],@"protected":@[@100]};},^{});
    ok &= checkCase(@"negative identity refuses",NO,^{return @{@"birth":@[@200,@123,@456,@(-1)],@"protected":@[@100]};},^{});
    ok &= checkCase(@"out-of-range identity refuses",NO,^{return @{@"birth":@[@200,@123,@1000000,@501],@"protected":@[@100]};},^{});
    ok &= checkCase(@"termination refused is not retried",NO,valid,^{refuseTermination=YES;});
    return ok?0:1;
} }
'''
OUT.mkdir(parents=True, exist_ok=True)
path = OUT / 'termination-tests.m'
path.write_text(prelude + guard + main)
subprocess.run(['clang', '-fobjc-arc', '-framework', 'Foundation', str(path),
                '-o', str(OUT/'termination-tests')], check=True)
raise SystemExit(subprocess.run([str(OUT/'termination-tests')]).returncode)
