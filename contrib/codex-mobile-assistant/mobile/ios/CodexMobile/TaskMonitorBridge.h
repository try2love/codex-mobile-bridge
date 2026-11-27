#import <Foundation/Foundation.h>
NS_ASSUME_NONNULL_BEGIN
@interface TaskMonitorBridge : NSObject
+ (NSString *)directory;
+ (BOOL)writeFile:(NSString *)name value:(NSDictionary *)value;
+ (NSString *)storageError;
+ (nullable NSDictionary *)readFile:(NSString *)name;
+ (int)startHelper;
+ (void)deliver:(NSDictionary *)event completion:(void (^)(BOOL))completion;
@end
NS_ASSUME_NONNULL_END
