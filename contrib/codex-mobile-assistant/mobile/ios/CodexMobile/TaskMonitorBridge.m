#import "TaskMonitorBridge.h"
#import "../TaskMonitor/TaskMonitorShared.h"
#import <TargetConditionals.h>
#import <dlfcn.h>
#import <spawn.h>
#import <sys/wait.h>
#import <fcntl.h>
#import <unistd.h>
#import <errno.h>

static NSString *TMBridgeStorageError = @"";

@implementation TaskMonitorBridge
+ (NSString *)directory { return TMDirectory(); }
+ (BOOL)writeFile:(NSString *)name value:(NSDictionary *)value {
    NSError *error = nil;
    NSString *directory = TMDirectoryWithError(&error);
    BOOL saved = directory.length && TMWriteJSONWithError(directory, name, value, &error);
    TMBridgeStorageError = saved ? @"" : (error.localizedDescription ?: @"写入后台提醒配置失败");
    return saved;
}
+ (NSString *)storageError { return TMBridgeStorageError; }
+ (NSDictionary *)readFile:(NSString *)name { return TMReadJSON(TMDirectory(), name); }
+ (int)startHelper {
#if TARGET_OS_SIMULATOR
    return ENOTSUP;
#else
    NSString *path = [NSBundle.mainBundle.bundlePath stringByAppendingPathComponent:@"Helpers/TaskMonitor"];
    if (access(path.fileSystemRepresentation, X_OK)) return errno;
    typedef int (*Persona)(const posix_spawnattr_t *, uid_t, uint32_t);
    typedef int (*Identity)(const posix_spawnattr_t *, uid_t);
    Persona setPersona = (Persona)dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_np");
    Identity setUID = (Identity)dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_uid_np");
    Identity setGID = (Identity)dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_gid_np");
    if (!setPersona || !setUID || !setGID) return ENOTSUP;
    static NSMutableArray<NSNumber *> *children;
    if (!children) children = [NSMutableArray array];
    for (NSNumber *pid in children.copy) {
        int status;
        if (waitpid(pid.intValue, &status, WNOHANG) != 0) [children removeObject:pid];
    }
    posix_spawnattr_t attr;
    posix_spawn_file_actions_t actions;
    posix_spawnattr_init(&attr);
    posix_spawn_file_actions_init(&actions);
    int error = setPersona(&attr, 99, 1);
    if (!error) error = setUID(&attr, 0);
    if (!error) error = setGID(&attr, 0);
    if (!error) error = posix_spawnattr_setpgroup(&attr, 0);
    if (!error) error = posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
    for (int fd = 0; fd < 3; fd++) posix_spawn_file_actions_addopen(&actions, fd, "/dev/null", O_RDWR, 0);
    const char *args[] = {path.fileSystemRepresentation, "--directory", TMDirectory().fileSystemRepresentation, NULL};
    char *environment[] = {NULL};
    pid_t pid = 0;
    if (!error) error = posix_spawn(&pid, path.fileSystemRepresentation, &actions, &attr, (char *const *)args, environment);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    if (!error) [children addObject:@(pid)];
    return error;
#endif
}
+ (void)deliver:(NSDictionary *)event completion:(void (^)(BOOL))completion {
    NSString *directory = TMDirectory();
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        BOOL accepted = TMDeliver(directory, UNUserNotificationCenter.currentNotificationCenter, event);
        dispatch_async(dispatch_get_main_queue(), ^{ completion(accepted); });
    });
}
@end
