#import "TaskMonitorShared.h"
#import <CommonCrypto/CommonDigest.h>
#import <TargetConditionals.h>
#import <sys/file.h>
#import <sys/stat.h>
#import <fcntl.h>
#import <unistd.h>
#import <limits.h>
#import <errno.h>

NSString *const TMAppIdentifier = @"io.github.abyxs.codexmobile";

static BOOL TMStorageFailure(NSError **error, NSString *stage, NSInteger code) {
    if (error) *error = [NSError errorWithDomain:@"TaskMonitorStorage" code:code
        userInfo:@{NSLocalizedDescriptionKey:[NSString stringWithFormat:@"%@（错误 %ld）", stage, (long)code]}];
    return NO;
}

NSString *TMDirectory(void) { return TMDirectoryWithError(NULL); }

NSString *TMDirectoryWithError(NSError **error) {
    if (error) *error = nil;
    NSString *directory = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/TaskMonitor"];
    NSError *cause = nil;
    if (![[NSFileManager defaultManager] createDirectoryAtPath:directory withIntermediateDirectories:YES
        attributes:@{NSFilePosixPermissions:@0700, NSFileProtectionKey:NSFileProtectionCompleteUntilFirstUserAuthentication}
        error:&cause]) {
        TMStorageFailure(error, @"创建后台提醒目录失败", cause.code);
        return @"";
    }
    // Foundation may strip /private; the validator and root helper use realpath.
    char resolved[PATH_MAX];
    if (!realpath(directory.fileSystemRepresentation, resolved)) {
        TMStorageFailure(error, @"解析后台提醒目录失败", errno);
        return @"";
    }
    return @(resolved);
}

BOOL TMValidateDirectory(NSString *directory) {
    char resolved[PATH_MAX];
    if (!realpath(directory.fileSystemRepresentation, resolved)) return NO;
    if (![directory isEqualToString:@(resolved)]) return NO;
#if !TARGET_OS_SIMULATOR
    NSArray *parts = directory.pathComponents;
    if (parts.count != 11 || ![directory hasPrefix:@"/private/var/mobile/Containers/Data/Application/"] ||
        ![[NSUUID alloc] initWithUUIDString:parts[7]] ||
        ![[parts subarrayWithRange:NSMakeRange(8, 3)] isEqual:@[@"Library", @"Application Support", @"TaskMonitor"]])
        return NO;
#endif
    struct stat st;
    return lstat(directory.fileSystemRepresentation, &st) == 0 && S_ISDIR(st.st_mode)
        && st.st_uid == (geteuid() == 0 ? 501 : geteuid()) && !(st.st_mode & 0077);
}

NSDictionary *TMReadJSON(NSString *directory, NSString *name) {
    if (!TMValidateDirectory(directory) || ![name isEqual:name.lastPathComponent]) return nil;
    int fd = open([directory stringByAppendingPathComponent:name].fileSystemRepresentation, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return nil;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 0 || st.st_size > 1024 * 1024 ||
        (st.st_uid != 501 && st.st_uid != geteuid()) || (st.st_mode & 0077)) { close(fd); return nil; }
    NSMutableData *data = [NSMutableData dataWithLength:(NSUInteger)st.st_size];
    NSUInteger offset = 0;
    while (offset < data.length) {
        ssize_t count = read(fd, (char *)data.mutableBytes + offset, data.length - offset);
        if (count <= 0) { close(fd); return nil; }
        offset += count;
    }
    close(fd);
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    return [value isKindOfClass:NSDictionary.class] ? value : nil;
}

BOOL TMWriteJSON(NSString *directory, NSString *name, NSDictionary *value) {
    return TMWriteJSONWithError(directory, name, value, NULL);
}

BOOL TMWriteJSONWithError(NSString *directory, NSString *name, NSDictionary *value, NSError **error) {
    if (error) *error = nil;
    if (!TMValidateDirectory(directory) || ![name isEqual:name.lastPathComponent])
        return TMStorageFailure(error, @"后台提醒目录校验失败", EINVAL);
    NSError *cause = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:&cause];
    if (!data) return TMStorageFailure(error, @"生成后台提醒配置失败", cause.code);
    if (data.length > 1024 * 1024) return TMStorageFailure(error, @"后台提醒配置过大", EFBIG);
    NSString *temporary = [directory stringByAppendingPathComponent:[@".write-" stringByAppendingString:NSUUID.UUID.UUIDString]];
    int fd = open(temporary.fileSystemRepresentation, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return TMStorageFailure(error, @"创建后台提醒文件失败", errno);
    NSUInteger offset = 0;
    BOOL ok = YES;
    while (offset < data.length) {
        ssize_t count = write(fd, (const char *)data.bytes + offset, data.length - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) {
            ok = TMStorageFailure(error, @"写入后台提醒文件失败", count < 0 ? errno : EIO);
            break;
        }
        offset += count;
    }
    if (ok && geteuid() == 0 && fchown(fd, 501, 501) != 0)
        ok = TMStorageFailure(error, @"设置后台提醒文件归属失败", errno);
    if (ok && fsync(fd) != 0)
        ok = TMStorageFailure(error, @"保存后台提醒文件失败", errno);
    int closed = close(fd);
    if (ok && closed != 0) ok = TMStorageFailure(error, @"关闭后台提醒文件失败", errno);
    // The helper must retain access after lock, but never before the first unlock after boot.
    if (ok && ![[NSFileManager defaultManager] setAttributes:
        @{NSFileProtectionKey:NSFileProtectionCompleteUntilFirstUserAuthentication}
        ofItemAtPath:temporary error:&cause])
        ok = TMStorageFailure(error, @"设置锁屏文件保护失败", cause.code);
    if (ok && rename(temporary.fileSystemRepresentation,
                     [directory stringByAppendingPathComponent:name].fileSystemRepresentation) != 0)
        ok = TMStorageFailure(error, @"替换后台提醒配置失败", errno);
    if (!ok) unlink(temporary.fileSystemRepresentation);
    return ok;
}

NSString *TMDigest(id value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value
        options:NSJSONWritingSortedKeys | NSJSONWritingWithoutEscapingSlashes error:nil];
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    NSMutableString *result = [NSMutableString string];
    for (NSUInteger i = 0; i < sizeof(digest); i++) [result appendFormat:@"%02x", digest[i]];
    return result;
}

BOOL TMDeliver(NSString *directory, UNUserNotificationCenter *center, NSDictionary *event) {
    NSString *origin = event[@"origin"], *host = event[@"host"], *chat = event[@"id"], *eventKey = event[@"eventKey"];
    if (![origin isKindOfClass:NSString.class] || ![host isKindOfClass:NSString.class] ||
        ![chat isKindOfClass:NSString.class] || ![[NSUUID alloc] initWithUUIDString:chat] ||
        ![eventKey isKindOfClass:NSString.class] || !eventKey.length || !TMValidateDirectory(directory)) return NO;
    NSString *lockPath = [directory stringByAppendingPathComponent:@"events.lock"];
    int lock = open(lockPath.fileSystemRepresentation, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (lock < 0) return NO;
    if (geteuid() == 0) fchown(lock, 501, 501);
    BOOL acquired = NO;
    for (int i = 0; i < 25 && !(acquired = flock(lock, LOCK_EX | LOCK_NB) == 0); i++) usleep(20000);
    if (!acquired) { close(lock); return NO; }
    BOOL accepted = NO;
    @try {
        NSDictionary *config = TMReadJSON(directory, @"config.json");
        if (event[@"generation"] && (![config[@"enabled"] boolValue] ||
            ![event[@"generation"] isEqual:config[@"generation"]])) return NO;

        NSMutableArray *keys = [NSMutableArray array];
        if ([eventKey hasPrefix:@"requests:"]) {
            id ids = [NSJSONSerialization JSONObjectWithData:[[eventKey substringFromIndex:9] dataUsingEncoding:NSUTF8StringEncoding]
                options:0 error:nil];
            if ([ids isKindOfClass:NSArray.class]) for (id requestID in ids) {
                if ([requestID isKindOfClass:NSString.class])
                    [keys addObject:TMDigest(@[origin, host, chat, [@"request:" stringByAppendingString:requestID]])];
            }
        }
        if (!keys.count) [keys addObject:TMDigest(@[origin, host, chat, eventKey])];
        NSDictionary *saved = TMReadJSON(directory, @"events.json");
        NSMutableArray *delivered = [saved[@"ids"] isKindOfClass:NSArray.class] ? [saved[@"ids"] mutableCopy] : [NSMutableArray array];
        BOOL fresh = NO;
        for (NSString *key in keys) if (![delivered containsObject:key]) { fresh = YES; break; }
        if (!fresh) return YES;

        UNMutableNotificationContent *content = [[UNMutableNotificationContent alloc] init];
        NSString *title = [event[@"title"] isKindOfClass:NSString.class] ? event[@"title"] : @"对话";
        NSString *message = [event[@"message"] isKindOfClass:NSString.class] ? event[@"message"] : @"任务有新状态";
        content.title = [title substringToIndex:MIN(title.length, 160)];
        content.body = [message substringToIndex:MIN(message.length, 300)];
        if ([event[@"sound"] boolValue]) content.sound = UNNotificationSound.defaultSound;
        content.threadIdentifier = [@"chat-" stringByAppendingString:TMDigest(@[origin, host, chat])];
        content.userInfo = @{@"origin":origin, @"id":chat, @"host":host};
        UNNotificationRequest *request = [UNNotificationRequest requestWithIdentifier:
            [@"task-" stringByAppendingString:TMDigest(keys)] content:content trigger:nil];
        dispatch_semaphore_t done = dispatch_semaphore_create(0);
        __block BOOL success = NO;
        [center addNotificationRequest:request withCompletionHandler:^(NSError *error) {
            success = error == nil;
            dispatch_semaphore_signal(done);
        }];
        if (dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 4 * NSEC_PER_SEC)) == 0 && success) {
            for (NSString *key in keys) if (![delivered containsObject:key]) [delivered addObject:key];
            if (delivered.count > 512) [delivered removeObjectsInRange:NSMakeRange(0, delivered.count - 512)];
            accepted = TMWriteJSON(directory, @"events.json", @{@"ids":delivered});
        }
    } @finally {
        flock(lock, LOCK_UN);
        close(lock);
    }
    return accepted;
}
