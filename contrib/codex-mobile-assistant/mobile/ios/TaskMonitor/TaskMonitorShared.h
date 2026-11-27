#import <Foundation/Foundation.h>
#import <UserNotifications/UserNotifications.h>

NS_ASSUME_NONNULL_BEGIN
FOUNDATION_EXPORT NSString *const TMAppIdentifier;
FOUNDATION_EXPORT NSString *TMDirectory(void);
FOUNDATION_EXPORT NSString *TMDirectoryWithError(NSError * _Nullable * _Nullable error);
FOUNDATION_EXPORT BOOL TMValidateDirectory(NSString *directory);
FOUNDATION_EXPORT NSDictionary * _Nullable TMReadJSON(NSString *directory, NSString *name);
FOUNDATION_EXPORT BOOL TMWriteJSON(NSString *directory, NSString *name, NSDictionary *value);
FOUNDATION_EXPORT BOOL TMWriteJSONWithError(NSString *directory, NSString *name, NSDictionary *value,
                                          NSError * _Nullable * _Nullable error);
FOUNDATION_EXPORT NSString *TMDigest(id value);
FOUNDATION_EXPORT BOOL TMDeliver(NSString *directory, UNUserNotificationCenter *center, NSDictionary *event);
NS_ASSUME_NONNULL_END
