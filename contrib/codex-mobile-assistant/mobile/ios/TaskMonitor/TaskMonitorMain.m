#import "TaskMonitorShared.h"
#import <sys/file.h>
#import <fcntl.h>
#import <unistd.h>

@interface UNUserNotificationCenter (TaskMonitorPrivate)
- (instancetype)initWithBundleIdentifier:(NSString *)identifier;
@end

@interface Monitor : NSObject <NSURLSessionTaskDelegate>
@property (nonatomic, copy) NSString *directory;
@property (nonatomic, copy) NSString *executable;
@property (nonatomic, copy) NSString *generation;
@property (nonatomic, strong) NSDictionary *config;
@property (nonatomic, strong) NSMutableDictionary *previous;
@property (nonatomic, strong) UNUserNotificationCenter *notifications;
@property (nonatomic, strong) NSURLSession *session;
@property (nonatomic, strong) NSURLSessionDataTask *request;
@property (nonatomic, strong) dispatch_source_t timer;
@property NSTimeInterval nextPoll;
@property NSTimeInterval lastPoll;
@property NSTimeInterval lastEvent;
@property (nonatomic, copy) NSString *status;
@property NSTimeInterval lastHeartbeat;
@property BOOL busy;
@property BOOL initialized;
@end

@implementation Monitor
- (void)writeStatus:(NSString *)status {
    NSTimeInterval now = NSDate.date.timeIntervalSince1970;
    if ([status isEqual:self.status] && now - self.lastHeartbeat < 5) return;
    self.lastHeartbeat = now;
    self.status = status;
    TMWriteJSON(self.directory, @"status.json", @{
        @"generation":self.generation ?: @"", @"state":status,
        @"updatedAt":@(NSDate.date.timeIntervalSince1970), @"lastPollAt":@(self.lastPoll),
        @"lastEventAt":@(self.lastEvent)});
}
- (BOOL)validConfig:(NSDictionary *)config {
    if (![config[@"enabled"] boolValue] || ![config[@"generation"] isKindOfClass:NSString.class] ||
        ![[NSUUID alloc] initWithUUIDString:config[@"generation"]] ||
        ![config[@"helper"] isEqualToString:self.executable] ||
        [config[@"expiresAt"] doubleValue] <= NSDate.date.timeIntervalSince1970) return NO;
    NSString *cookie = config[@"cookie"];
    if (![cookie isKindOfClass:NSString.class] || ![cookie hasPrefix:@"codex_mobile_session="] ||
        cookie.length > 8192 || [cookie rangeOfCharacterFromSet:NSCharacterSet.newlineCharacterSet].location != NSNotFound)
        return NO;
    NSURLComponents *parts = [NSURLComponents componentsWithString:config[@"origin"] ?: @""];
    return [parts.scheme isEqualToString:@"https"] && parts.host.length && !parts.user && !parts.password &&
        !parts.query && !parts.fragment && (!parts.path.length || [parts.path isEqualToString:@"/"]);
}
- (void)start {
    self.config = TMReadJSON(self.directory, @"config.json");
    if (![self validConfig:self.config]) exit(0);
    self.generation = self.config[@"generation"];
    NSDictionary *saved = TMReadJSON(self.directory, @"watch.json");
    self.initialized = [saved[@"scope"] isEqual:self.config[@"scope"]];
    self.previous = self.initialized && [saved[@"tasks"] isKindOfClass:NSDictionary.class]
        ? [saved[@"tasks"] mutableCopy] : [NSMutableDictionary dictionary];
    if (![UNUserNotificationCenter instancesRespondToSelector:@selector(initWithBundleIdentifier:)]) {
        [self writeStatus:@"unsupported"]; exit(0);
    }
    self.notifications = [[UNUserNotificationCenter alloc] initWithBundleIdentifier:TMAppIdentifier];
    if (!self.notifications) { [self writeStatus:@"unsupported"]; exit(0); }
    NSURLSessionConfiguration *network = NSURLSessionConfiguration.ephemeralSessionConfiguration;
    network.HTTPCookieStorage = nil;
    network.HTTPShouldSetCookies = NO;
    network.URLCache = nil;
    network.timeoutIntervalForRequest = 10;
    network.timeoutIntervalForResource = 15;
    self.session = [NSURLSession sessionWithConfiguration:network delegate:self delegateQueue:nil];
    [self writeStatus:@"starting"];
    self.timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
    dispatch_source_set_timer(self.timer, DISPATCH_TIME_NOW, NSEC_PER_SEC, NSEC_PER_SEC / 4);
    dispatch_source_set_event_handler(self.timer, ^{ [self tick]; });
    dispatch_resume(self.timer);
}
- (void)tick {
    NSDictionary *config = TMReadJSON(self.directory, @"config.json");
    if (![self validConfig:config] || ![config[@"generation"] isEqual:self.generation]) {
        [self.request cancel];
        [self writeStatus:@"stopped"];
        exit(0);
    }
    self.config = config;
    [self writeStatus:self.status ?: @"starting"];
    if (self.busy || NSDate.date.timeIntervalSince1970 < self.nextPoll) return;
    self.busy = YES;
    NSDictionary *snapshot = self.config;
    NSURL *url = [NSURL URLWithString:@"/api/task-status?switcher=true" relativeToURL:[NSURL URLWithString:snapshot[@"origin"]]].absoluteURL;
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url];
    [request setValue:snapshot[@"cookie"] forHTTPHeaderField:@"Cookie"];
    [request setValue:@"application/json" forHTTPHeaderField:@"Accept"];
    self.request = [self.session dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        // Work is sequential, but never blocks the main-loop stop/config heartbeat.
        NSInteger code = [(NSHTTPURLResponse *)response statusCode];
        NSDictionary *current = TMReadJSON(self.directory, @"config.json");
        if (![current[@"enabled"] boolValue] || ![current[@"generation"] isEqual:self.generation]) return;
        NSString *state = @"network";
        if (code == 401 || code == 403) state = @"login";
        else if (!error && code == 200 && data.length > 0 && data.length < 2 * 1024 * 1024) {
            id body = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
            if ([body isKindOfClass:NSDictionary.class] && [body[@"tasks"] isKindOfClass:NSArray.class]) {
                state = [self consume:body[@"tasks"] config:snapshot] ? @"listening" : @"notification-error";
            }
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            self.busy = NO;
            if ([state isEqual:@"listening"] || [state isEqual:@"notification-error"]) self.lastPoll = NSDate.date.timeIntervalSince1970;
            self.nextPoll = NSDate.date.timeIntervalSince1970 + ([state isEqual:@"network"] ? 15 : 5);
            [self writeStatus:state];
            if ([state isEqual:@"login"]) exit(0);
        });
    }];
    [self.request resume];
}
- (BOOL)consume:(NSArray *)tasks config:(NSDictionary *)config {
    BOOL allAccepted = YES;
    NSUInteger count = 0;
    for (id object in tasks) {
        if (++count > 300) break;
        if (![object isKindOfClass:NSDictionary.class]) continue;
        NSDictionary *row = object;
        NSString *chat = row[@"id"], *host = row[@"host"], *token = row[@"token"], *phase = row[@"phase"];
        if (![chat isKindOfClass:NSString.class] || ![[NSUUID alloc] initWithUUIDString:chat] ||
            ![host isKindOfClass:NSString.class] || !host.length || host.length > 512 ||
            ![token isKindOfClass:NSString.class] || token.length > 16384 ||
            ![@[@"running", @"completed", @"attention"] containsObject:phase]) continue;
        NSString *key = TMDigest(@[host, chat]);
        NSDictionary *prior = self.previous[key];
        NSMutableArray<NSString *> *requests = [NSMutableArray array];
        if ([row[@"requests"] isKindOfClass:NSArray.class]) for (id rid in row[@"requests"]) {
            if ([rid isKindOfClass:NSString.class] || [rid isKindOfClass:NSNumber.class])
                [requests addObject:[rid description]];
        }
        [requests sortUsingSelector:@selector(compare:)];
        NSString *turn = [[token componentsSeparatedByString:@":"] firstObject];
        NSString *eventKey = nil, *message = nil;
        if (requests.count) {
            NSData *ids = [NSJSONSerialization dataWithJSONObject:requests options:NSJSONWritingWithoutEscapingSlashes error:nil];
            eventKey = [@"requests:" stringByAppendingString:[[NSString alloc] initWithData:ids encoding:NSUTF8StringEncoding]];
            message = [row[@"actionKind"] isEqual:@"reply"] ? @"等你回复" :
                [row[@"actionKind"] isEqual:@"approval"] ? @"等待你的授权" : @"需要你的确认";
        } else if (![phase isEqual:@"running"] && turn.length) {
            // The first snapshot is a baseline, not a replay of historical completions.
            BOOL changed = prior && ![prior[@"token"] isEqual:token];
            BOOL createdWhileWatching = self.initialized && [row[@"at"] respondsToSelector:@selector(doubleValue)] &&
                [row[@"at"] doubleValue] >= [config[@"startedAt"] doubleValue];
            if (changed || (!prior && createdWhileWatching)) {
                eventKey = [@"end:" stringByAppendingString:turn];
                message = [row[@"error"] boolValue] ? @"任务结束，请查看错误" : @"任务已完成";
            }
        }
        BOOL accepted = YES;
        if (eventKey) {
            NSDictionary *latest = TMReadJSON(self.directory, @"config.json");
            if (![latest[@"enabled"] boolValue] || ![latest[@"generation"] isEqual:self.generation]) return NO;
            accepted = TMDeliver(self.directory, self.notifications, @{
                @"origin":config[@"origin"], @"host":host, @"id":chat, @"eventKey":eventKey,
                @"title":[row[@"title"] isKindOfClass:NSString.class] ? row[@"title"] : @"对话",
                @"message":message, @"sound":latest[@"sound"] ?: @YES, @"generation":self.generation});
            if (accepted) dispatch_async(dispatch_get_main_queue(), ^{ self.lastEvent = NSDate.date.timeIntervalSince1970; });
        }
        if (accepted) self.previous[key] = @{@"token":token};
        else allAccepted = NO; // Retry after permission/network recovery, without consuming the event.
    }
    self.initialized = YES;
    if (self.previous.count > 400) {
        // Retain only bounded state; the separate event ledger prevents repeated alerts.
        while (self.previous.count > 400) [self.previous removeObjectForKey:self.previous.allKeys.firstObject];
    }
    TMWriteJSON(self.directory, @"watch.json", @{@"scope":config[@"scope"], @"tasks":self.previous});
    return allAccepted;
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
    completionHandler:(void (^)(NSURLRequest *))completionHandler {
    completionHandler(nil); // Never forward the session cookie to a redirected host.
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 3 || strcmp(argv[1], "--directory") || geteuid() != 0) return 2;
        NSString *directory = @(argv[2]);
        if (!TMValidateDirectory(directory)) return 3;
        int lock = open([directory stringByAppendingPathComponent:@"monitor.lock"].fileSystemRepresentation,
            O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (lock < 0) return 4;
        fchown(lock, 501, 501);
        if (flock(lock, LOCK_EX | LOCK_NB)) { close(lock); return 0; }
        Monitor *monitor = [[Monitor alloc] init];
        monitor.directory = directory;
        monitor.executable = [@(argv[0]) stringByResolvingSymlinksInPath];
        [monitor start];
        dispatch_main();
        flock(lock, LOCK_UN);
        close(lock);
    }
    return 0;
}
