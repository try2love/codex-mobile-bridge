#include <jni.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef int32_t (*save_fn)(const char*,void*);
extern char *cmb_cloud_call(const char*,const char*,save_fn,void*);
extern void cmb_cloud_free(char*);
struct call_context { JNIEnv *env; jobject owner; jmethodID save; };
static int32_t save_state(const char *text, void *opaque){
    struct call_context *ctx=opaque; JNIEnv *env=ctx->env;
    size_t n=strlen(text);
    if(n>4*1024*1024)return 0;
    jbyteArray bytes=(*env)->NewByteArray(env,(jsize)n);
    if(!bytes)return 0;
    (*env)->SetByteArrayRegion(env,bytes,0,(jsize)n,(const jbyte*)text);
    jboolean saved=(*env)->CallBooleanMethod(env,ctx->owner,ctx->save,bytes);
    (*env)->DeleteLocalRef(env,bytes);
    return (*env)->ExceptionCheck(env)?0:saved;
}
static char *text_bytes(JNIEnv *env,jbyteArray bytes){
    jsize n=(*env)->GetArrayLength(env,bytes);
    if(n>4*1024*1024)return NULL;
    char *s=calloc(1,(size_t)n+1);
    if(s)(*env)->GetByteArrayRegion(env,bytes,0,n,(jbyte*)s);
    return s;
}
JNIEXPORT jbyteArray JNICALL Java_io_github_try2love_codexbridge_CloudNative_dispatch
(JNIEnv *env,jobject owner,jbyteArray request,jbyteArray state){
    struct call_context ctx={env,owner,(*env)->GetMethodID(env,(*env)->GetObjectClass(env,owner),"saveState","([B)Z")};
    if(!ctx.save)return NULL;
    char *cmd=text_bytes(env,request),*vault=text_bytes(env,state);
    char *answer=(cmd&&vault)?cmb_cloud_call(cmd,vault,save_state,&ctx):NULL;
    if(cmd){memset(cmd,0,strlen(cmd));free(cmd);}if(vault){memset(vault,0,strlen(vault));free(vault);}
    if(!answer)return NULL;
    size_t n=strlen(answer);jbyteArray output=(*env)->NewByteArray(env,(jsize)n);
    if(output)(*env)->SetByteArrayRegion(env,output,0,(jsize)n,(const jbyte*)answer);
    cmb_cloud_free(answer);return output;
}
