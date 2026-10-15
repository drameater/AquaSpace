#!/usr/bin/env python3
"""Compile the actual native foreground guard against deterministic API stubs.

No real AX, AppKit application/workspace, process or focus API is executed.
Only Foundation/CoreFoundation objects implement the fake retained AX roots.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.debug/ghostty-foreground-offline'
OUT.mkdir(parents=True, exist_ok=True)
source = (ROOT / 'script/native-tabs-smoke/ghostty-topology/ScriptDriver.m').read_text()
guard = source[source.index('@interface AquaSpaceGhosttyForegroundGuard'):source.index('// Selectors follow')]
prelude = r'''
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
static pid_t livePid=200, cachedPid=100, windowPid=200;
static BOOL axFront=YES, badBoolean=NO, readFailure=NO, titleChanged=NO, windowless=NO;
static OSStatus frontError=0, pidError=0;
static CFStringRef fakeApp, originalWindow, replacementWindow;
static CFTypeRef currentWindow;
@interface FakeRunningApplication : NSObject
@property pid_t processIdentifier;
@property BOOL isActive;
@end
@implementation FakeRunningApplication
@end
@interface FakeWorkspace : NSObject
+ (instancetype)sharedWorkspace;
- (FakeRunningApplication *)frontmostApplication;
@end
@implementation FakeWorkspace
+ (instancetype)sharedWorkspace { static FakeWorkspace *v; if(!v)v=[FakeWorkspace new]; return v; }
- (FakeRunningApplication *)frontmostApplication {
    FakeRunningApplication *v=[FakeRunningApplication new];v.processIdentifier=cachedPid;v.isActive=YES;return v;
}
@end
static CFTypeID FakeAXType(void) { return CFStringGetTypeID(); }
static AXError FakePid(AXUIElementRef element,pid_t *pid) {
    *pid=((CFTypeRef)element==fakeApp)?200:windowPid;return kAXErrorSuccess;
}
static AXError FakeTimeout(AXUIElementRef app,float timeout) { return kAXErrorSuccess; }
static AXError FakeCopy(AXUIElementRef app,CFStringRef attr,CFTypeRef *result) {
    *result=NULL;
    if(CFEqual(attr,kAXTitleAttribute)){*result=CFRetain(titleChanged?CFSTR("changed"):CFSTR("same"));return kAXErrorSuccess;}
    if(CFEqual(attr,kAXFocusedWindowAttribute)) {
        if(currentWindow)*result=CFRetain(currentWindow);
        return currentWindow?kAXErrorSuccess:kAXErrorNoValue;
    }
    if(CFEqual(attr,kAXFrontmostAttribute)) {
        if(readFailure)return kAXErrorCannotComplete;
        *result=CFRetain(badBoolean?(CFTypeRef)CFSTR("true"):(CFTypeRef)(axFront?kCFBooleanTrue:kCFBooleanFalse));return kAXErrorSuccess;
    }
    abort(); // Any unexpected read is a test failure, never a real API fallback.
}
static OSStatus FakeFront(ProcessSerialNumber *serial) { serial->highLongOfPSN=0;serial->lowLongOfPSN=livePid;return frontError; }
static OSStatus FakeProcessPid(const ProcessSerialNumber *serial,pid_t *pid) { *pid=(pid_t)serial->lowLongOfPSN;return pidError; }
#define AXUIElementGetTypeID FakeAXType
#define AXUIElementGetPid FakePid
#define AXUIElementSetMessagingTimeout FakeTimeout
#define AXUIElementCopyAttributeValue FakeCopy
#define NSWorkspace FakeWorkspace
#define NSRunningApplication FakeRunningApplication
#define GetFrontProcess FakeFront
#define GetProcessPID FakeProcessPid
'''
main = r'''
static AquaSpaceGhosttyForegroundGuard *makeGuard(void) {
 return [[AquaSpaceGhosttyForegroundGuard alloc] initWithSnapshot:@{@"pid":@200,@"title":@"same",@"hasWindow":@(!windowless)} application:(AXUIElementRef)fakeApp window:windowless?NULL:(AXUIElementRef)originalWindow];
}
static BOOL checkCase(NSString *name,BOOL shouldPass,void(^setup)(void),void(^afterBind)(void)) {
 livePid=200;cachedPid=200;windowPid=200;axFront=YES;badBoolean=NO;readFailure=NO;titleChanged=NO;
 frontError=0;pidError=0;windowless=NO;currentWindow=originalWindow;
 setup();BOOL passed=NO;
 @autoreleasepool { @try { AquaSpaceGhosttyForegroundGuard *guard=makeGuard();if(afterBind)afterBind();[guard check];passed=YES; }
 @catch(NSException *error) { passed=NO; } }
 if(passed!=shouldPass){fprintf(stderr,"FAIL: %s (accepted=%d, expected=%d)\n",name.UTF8String,passed,shouldPass);return NO;}
 printf("PASS: %s\n",name.UTF8String);return YES;
}
int main(void) { @autoreleasepool {
 fakeApp=CFSTR("retained-app");originalWindow=CFSTR("original-root");replacementWindow=CFSTR("replacement-root");
 BOOL ok=YES;
 ok &= checkCase(@"stale AppKit cache cannot reject correct live source",YES,^{cachedPid=100;},nil);
 ok &= checkCase(@"stale matching cache cannot accept different live foreground",NO,^{livePid=100;},nil);
 ok &= checkCase(@"late real foreground departure refuses",NO,^{},^{livePid=100;});
 ok &= checkCase(@"same-PID same-title replacement cannot replace retained root",NO,^{},^{currentWindow=replacementWindow;});
 ok &= checkCase(@"changed retained title refuses",NO,^{},^{titleChanged=YES;});
 ok &= checkCase(@"AX background source refuses despite matching system PID",NO,^{axFront=NO;},nil);
 ok &= checkCase(@"nonboolean AX foreground reply refuses",NO,^{badBoolean=YES;},nil);
 ok &= checkCase(@"unreadable AX foreground refuses",NO,^{readFailure=YES;},nil);
 ok &= checkCase(@"unreadable system foreground refuses",NO,^{frontError=-50;},nil);
 ok &= checkCase(@"unreadable system PID refuses",NO,^{pidError=-50;},nil);
 ok &= checkCase(@"foreign-PID focused root refuses",NO,^{windowPid=999;},nil);
 ok &= checkCase(@"windowless exact foreground source is allowed",YES,^{windowless=YES;currentWindow=NULL;},nil);
 ok &= checkCase(@"windowless source gaining a window refuses",NO,^{windowless=YES;currentWindow=NULL;},^{currentWindow=originalWindow;});
 return ok?0:1;
} }
'''
path = OUT / 'guard-tests.m'
path.write_text(prelude + guard + main)
subprocess.run(['clang', '-fobjc-arc', '-framework', 'Foundation', str(path), '-o', str(OUT/'guard-tests')], check=True)
raise SystemExit(subprocess.run([str(OUT/'guard-tests')]).returncode)
