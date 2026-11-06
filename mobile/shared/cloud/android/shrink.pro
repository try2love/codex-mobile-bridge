-dontobfuscate
-dontoptimize
-keep class io.github.try2love.codexbridge.** { *; }
-keep class com.google.zxing.** { *; }
# JNI and Android's classloader resolve the verifier and its upcalls by name.
-keep class org.rustls.platformverifier.** { *; }
# The Kotlin standard library uses these CLASS-retention nullability annotations at compile time.
-dontwarn org.jetbrains.annotations.NotNull
-dontwarn org.jetbrains.annotations.Nullable
