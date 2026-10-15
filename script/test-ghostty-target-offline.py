#!/usr/bin/env python3
"""Actual target-admission code with fake kernel, foreground and Ghostty APIs.

Build/run only a Foundation test binary, never a diagnostic helper or live API.
--source permits replaying the same tests against a saved pre-fix source file.
"""
import argparse
import ast
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,default=ROOT/'script/native-tabs-smoke/ghostty-topology/ScriptDriver.m')
source=parser.parse_args().source.read_text()
tree=ast.parse((ROOT/'script/test-ghostty-termination-offline.py').read_text())
prelude=ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='prelude' for t in n.targets)))
prelude=prelude.replace('static NSArray *windows(GhosttyApplication *app)', 'static NSArray *cleanupWindows(GhosttyApplication *app)')
fakes=r'''
static int sourceReads,changeOnSourceRead,mutations,titleWrites;
static BOOL changeAfterCreate;
@interface GhosttyObject (TargetFake)
- (NSString *)id; - (NSString *)name; - (NSArray *)tabs;
- (id)selectedTab; - (id)focusedTerminal; - (void)selectTab;
@end
@interface OwnedObject : GhosttyObject
@property NSString *kind;
@end
static OwnedObject *object(NSString *kind) { OwnedObject *v=[OwnedObject new];v.kind=kind;return v; }
@implementation OwnedObject
- (NSString *)id { return [self.kind stringByAppendingString:@"-a"]; }
- (NSString *)name { return @"AeroSpace-NativeTabs-offline-target-a"; }
- (NSArray *)tabs { return @[object(@"tab")]; }
- (id)selectedTab { return object(@"tab"); }
- (id)focusedTerminal { return object(@"terminal"); }
- (void)selectTab { mutations++; }
@end
@interface GhosttyApplication (TargetFake)
- (id)newWindowWithConfiguration:(id)c;
- (id)newTabIn:(id)w withConfiguration:(id)c;
- (BOOL)performAction:(NSString *)action on:(id)terminal;
@end
@implementation GhosttyApplication (TargetFake)
- (id)newWindowWithConfiguration:(id)c { mutations++;if(changeAfterCreate)liveBirth++;return object(@"window"); }
- (id)newTabIn:(id)w withConfiguration:(id)c { mutations++;return object(@"tab"); }
- (BOOL)performAction:(NSString *)action on:(id)terminal { titleWrites++;return YES; }
@end
@interface FakeRunningApplication (TargetFake)
- (BOOL)activateWithOptions:(NSUInteger)options;
@end
@implementation FakeRunningApplication (TargetFake)
- (BOOL)activateWithOptions:(NSUInteger)options { mutations++;return YES; }
@end
@interface AquaSpaceGhosttyForegroundGuard : NSObject
- (instancetype)initWithSnapshot:(NSDictionary *)snapshot application:(AXUIElementRef)app window:(AXUIElementRef)window;
- (void)check;
@end
@implementation AquaSpaceGhosttyForegroundGuard
- (instancetype)initWithSnapshot:(NSDictionary *)snapshot application:(AXUIElementRef)app window:(AXUIElementRef)window {
    self=[super init];if(self)[self check];return self;
}
- (void)check {
    // The original foreground source stays valid while the TARGET changes.
    sourceReads++;if(changeOnSourceRead==sourceReads)liveBirth++;
}
@end
static id value(id v) { return v; }
static NSString *identity(id v) { return [(GhosttyObject *)v id]; }
static NSArray *tabs(id w) { return [(GhosttyObject *)w tabs]; }
static NSArray *windows(GhosttyApplication *app) {
    NSArray *read=cleanupWindows(app);
    if(![read isKindOfClass:NSArray.class])return read;
    return @[object(@"window")];
}
'''
describe=source[source.index('static NSDictionary *describe'):source.index('// Termination is a background-only operation.')]
guard=source[source.index('// Termination is a background-only operation.'):source.index('static NSDictionary *performOperation')]
operation=source[source.index('static NSDictionary *performOperation'):source.index('// There is deliberately no executable')]
main=r'''
static BOOL checkCase(NSString *name,NSString *op,BOOL shouldPass,int expectedEffects,void(^setup)(NSMutableDictionary *)) {
    liveBirth=123;liveUid=501;infoReads=pathReads=windowReads=bindings=terminations=0;
    shortInfo=deniedPath=wrongPath=zombie=foreignBundle=badNativePath=nativeTerminated=NO;
    unownedWindow=unreadableWindows=malformedWindows=refuseTermination=NO;
    changeOnWindow=unownedOnWindow=changeOnInfo=0;changeOnPath=changeOnBind=NO;
    sourceReads=changeOnSourceRead=mutations=titleWrites=0;changeAfterCreate=NO;
    NSArray *args;
    if([op isEqual:@"create"])args=@[@"200",op,@"AeroSpace-NativeTabs-offline-target-a"];
    else if([op isEqual:@"add"])args=@[@"200",op,@"AeroSpace-NativeTabs-offline-target-a",@"tab-a",@"terminal-a"];
    else if([op isEqual:@"owned-status"])args=@[@"200",op];
    else args=@[@"200",op,@"tab-a",@"terminal-a"];
    NSMutableDictionary *request=[@{@"argv":args,@"foreground":@{},@"process":@{@"birth":@[@200,@123,@456,@501],@"protected":@[@100]}} mutableCopy];
    setup(request);BOOL passed=NO;
    @try { performOperation(request,NULL,NULL);passed=YES; } @catch(NSException *error) { passed=NO; }
    if(passed!=shouldPass || mutations!=expectedEffects || (!shouldPass && titleWrites)) {
        fprintf(stderr,"FAIL: %s (%s accepted=%d expected=%d effects=%d titles=%d)\n",name.UTF8String,op.UTF8String,passed,shouldPass,mutations,titleWrites);return NO;
    }
    printf("PASS: %s (%s)\n",name.UTF8String,op.UTF8String);return YES;
}
int main(void) { @autoreleasepool {
    BOOL ok=YES;
    for(NSString *op in @[@"create",@"add",@"select",@"activate"]) {
        ok &= checkCase(@"unchanged pinned target",op,YES,1,^(NSMutableDictionary *r){});
        ok &= checkCase(@"target replaced before entry",op,NO,0,^(NSMutableDictionary *r){liveBirth=999;});
        ok &= checkCase(@"target replaced during original-source capture",op,NO,0,^(NSMutableDictionary *r){changeOnSourceRead=1;});
        ok &= checkCase(@"target replaced during final original-source read",op,NO,0,^(NSMutableDictionary *r){changeOnSourceRead=2;});
        ok &= checkCase(@"target replaced during native binding",op,NO,0,^(NSMutableDictionary *r){changeOnBind=YES;});
        ok &= checkCase(@"missing target proof",op,NO,0,^(NSMutableDictionary *r){[r removeObjectForKey:@"process"];});
        ok &= checkCase(@"protected target",op,NO,0,^(NSMutableDictionary *r){r[@"process"]=@{@"birth":@[@200,@123,@456,@501],@"protected":@[@200]};});
        ok &= checkCase(@"wrong target executable",op,NO,0,^(NSMutableDictionary *r){wrongPath=YES;});
    }
    for(NSString *op in @[@"add",@"select",@"activate"])
        ok &= checkCase(@"target replaced during exact-anchor enumeration",op,NO,0,^(NSMutableDictionary *r){changeOnWindow=1;});
    ok &= checkCase(@"replacement after creation prevents title write",@"create",NO,1,^(NSMutableDictionary *r){changeAfterCreate=YES;});
    ok &= checkCase(@"owned membership read",@"owned-status",YES,0,^(NSMutableDictionary *r){});
    ok &= checkCase(@"owned read missing proof",@"owned-status",NO,0,^(NSMutableDictionary *r){[r removeObjectForKey:@"process"];});
    ok &= checkCase(@"owned read rejects replacement during enumeration",@"owned-status",NO,0,^(NSMutableDictionary *r){changeOnWindow=1;});
    ok &= checkCase(@"owned read rejects unreadable windows",@"owned-status",NO,0,^(NSMutableDictionary *r){unreadableWindows=YES;});
    return ok?0:1;
} }
'''
out=ROOT/'.debug/ghostty-target-offline'
out.mkdir(parents=True,exist_ok=True)
path=out/'target-tests.m';path.write_text(prelude+fakes+describe+guard+operation+main)
subprocess.run(['clang','-fobjc-arc','-Wno-objc-protocol-method-implementation','-framework','Foundation',str(path),'-o',str(out/'target-tests')],check=True)
raise SystemExit(subprocess.run([str(out/'target-tests')]).returncode)
