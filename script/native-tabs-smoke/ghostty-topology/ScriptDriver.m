#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <ScriptingBridge/ScriptingBridge.h>

// Selectors follow the installed Ghostty.sdef; every event targets a specific PID.
@interface GhosttyApplication : SBApplication
- (SBElementArray *)windows;
- (id)newWindowWithConfiguration:(id)configuration __attribute__((ns_returns_not_retained));
- (id)newTabIn:(id)window withConfiguration:(id)configuration __attribute__((ns_returns_not_retained));
- (BOOL)performAction:(NSString *)action on:(id)terminal;
- (void)selectTab:(id)tab;
- (void)closeTab:(id)tab;
- (void)activateWindow:(id)window;
@end
@interface GhosttyObject : SBObject
- (NSString *)id;
- (NSString *)name;
- (SBElementArray *)tabs;
- (id)selectedTab;
- (id)focusedTerminal;
- (void)selectTab;
- (void)activateWindow;
- (void)closeTab;
@end
static id value(id object) { return [object respondsToSelector:@selector(get)] ? [object get] : object; }
static NSString *identity(id object) { return (NSString *)value([(GhosttyObject *)object id]); }
static NSArray *windows(GhosttyApplication *app) { return (NSArray *)value([app windows]); }
static NSArray *tabs(id window) { return (NSArray *)value([(GhosttyObject *)window tabs]); }
static NSDictionary *describe(GhosttyApplication *app) {
    NSMutableArray *groups = [NSMutableArray array];
    for (id w in windows(app)) {
        NSMutableArray *members = [NSMutableArray array];
        for (id t in tabs(w)) {
            id terminal = value([(GhosttyObject *)t focusedTerminal]);
            [members addObject:@{@"tab":identity(t), @"terminal":identity(terminal), @"title":value([(GhosttyObject *)t name]) ?: @""}];
        }
        id selected = value([(GhosttyObject *)w selectedTab]);
        [groups addObject:@{@"window":identity(w), @"selected":identity(selected), @"members":members}];
    }
    return @{@"groups":groups};
}
int main(int argc, const char *argv[]) { @autoreleasepool { @try {
    if (argc < 3) [NSException raise:@"Arguments" format:@"Expected PID and operation"];
    pid_t pid = (pid_t)atoi(argv[1]); NSString *op = @(argv[2]);
    NSRunningApplication *native = [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
    if (![native.bundleIdentifier isEqual:@"com.mitchellh.ghostty"]) [NSException raise:@"Identity" format:@"Not the expected Ghostty PID"];
    GhosttyApplication *app = (GhosttyApplication *)[SBApplication applicationWithProcessIdentifier:pid];
    app.timeout = 10 * 60;
    NSDictionary *result;
    if ([op isEqual:@"status"]) { result = describe(app); }
    else if ([op isEqual:@"create"] || [op isEqual:@"add"]) {
        if (argc < 4) [NSException raise:@"Arguments" format:@"Missing test title"];
        NSString *title = @(argv[3]);
        if (![title hasPrefix:@"AeroSpace-NativeTabs-"]) [NSException raise:@"Title" format:@"Missing test prefix"];
        id targetWindow = nil;
        if ([op isEqual:@"add"]) {
            if (argc != 6) [NSException raise:@"Arguments" format:@"Missing anchor identities"];
            for (id w in windows(app)) for (id t in tabs(w)) if ([identity(t) isEqual:@(argv[4])]) {
                if (![identity(value([(GhosttyObject *)t focusedTerminal])) isEqual:@(argv[5])]) [NSException raise:@"Identity" format:@"Terminal changed"];
                targetWindow = w;
            }
            if (!targetWindow) [NSException raise:@"Identity" format:@"Anchor tab absent"];
        }
        id tab = [op isEqual:@"create"] ? value([(GhosttyObject *)[app newWindowWithConfiguration:nil] selectedTab]) : [app newTabIn:targetWindow withConfiguration:nil];
        tab = value(tab); id terminal = value([(GhosttyObject *)tab focusedTerminal]);
        if (![app performAction:[@"set_tab_title:" stringByAppendingString:title] on:terminal]) [NSException raise:@"Title" format:@"Could not assign test title"];
        result = @{@"tab":identity(tab), @"terminal":identity(terminal), @"title":title};
    } else {
        if (argc != 5) [NSException raise:@"Arguments" format:@"Missing exact tab/terminal identities"];
        id targetWindow = nil, targetTab = nil;
        for (id w in windows(app)) for (id t in tabs(w)) if ([identity(t) isEqual:@(argv[3])]) {
            if (![identity(value([(GhosttyObject *)t focusedTerminal])) isEqual:@(argv[4])]) [NSException raise:@"Identity" format:@"Terminal changed"];
            targetWindow=w; targetTab=t;
        }
        if (!targetTab) {
            if ([op isEqual:@"close"]) result=@{@"closed":@YES,@"alreadyAbsent":@YES};
            else [NSException raise:@"Identity" format:@"Test tab absent"];
        } else if ([op isEqual:@"select"]) {
            [(GhosttyObject *)targetTab selectTab]; [(GhosttyObject *)targetWindow activateWindow];
            [native activateWithOptions:NSApplicationActivateIgnoringOtherApps]; result=describe(app);
        } else if ([op isEqual:@"close"]) { [(GhosttyObject *)targetTab closeTab]; result=@{@"closed":@YES}; }
        else [NSException raise:@"Operation" format:@"Unknown operation"];
    }
    NSError *error=nil; NSData *data=[NSJSONSerialization dataWithJSONObject:result options:0 error:&error];
    if (!data) [NSException raise:@"Serialization" format:@"%@",error];
    fwrite(data.bytes,1,data.length,stdout); fputc('\n',stdout); return 0;
} @catch (NSException *exception) { fprintf(stderr,"%s: %s\n",exception.name.UTF8String,exception.reason.UTF8String); return 2; } } }
