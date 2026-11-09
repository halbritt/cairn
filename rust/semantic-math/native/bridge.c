#define _GNU_SOURCE
#include "internal.h"
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

_Static_assert(sizeof(void *)==8 && sizeof(size_t)==8 && sizeof(int)==4,"selected GNU x86_64 ABI");
_Static_assert(sizeof(blasint)==8 && sizeof(CBLAS_ORDER)==4 && sizeof(CBLAS_TRANSPOSE)==4,"header-selected ILP64/CBLAS ABI");
_Static_assert(CblasColMajor==102 && CblasTrans==112,"NumPy2.4.6 npy_cblas enum agreement");
_Static_assert(sizeof(CairnMathError)==32 && offsetof(CairnMathError,stage)==8,"error bridge ABI");
_Static_assert(sizeof(CairnMathBytes)==16 && sizeof(CairnMathModule)==32 && sizeof(CairnMathSettings)==24,"record bridge ABI");

int cairn_math_error(CairnMathError *e,int code,const char *stage,const char *message) {
    if (!e->stage) {e->stage=stage;e->code=code;}
    size_t old=e->message ? strlen(e->message) : 0,a=strlen(stage),b=strlen(message);
    char codes[24];int n=snprintf(codes,sizeof(codes),"[%d] ",code);
    if (n<0 || (size_t)n>=sizeof(codes)) {e->copy_failed=1;return -1;}
    size_t c=(size_t)n;
    if (old>SIZE_MAX-a-4 || old+a+4>SIZE_MAX-b-1 || old+a+b+5>SIZE_MAX-c) {e->copy_failed=1;return -1;}
    char *next=malloc(old+a+b+c+5);
    if (!next) {e->copy_failed=1;return -1;}
    size_t at=0;
    if (old) {memcpy(next,e->message,old);at=old;memcpy(next+at,"; ",2);at+=2;}
    memcpy(next+at,stage,a);at+=a;memcpy(next+at,": ",2);at+=2;
    memcpy(next+at,codes,c);at+=c;memcpy(next+at,message,b+1);
    free(e->message);e->message=next;return -1;
}
static int system_error(CairnMathError *e,const char *stage) {int code=errno;return cairn_math_error(e,code,stage,strerror(code));}
static int loader_error(CairnMathError *e,const char *stage) {const char *s=dlerror();return cairn_math_error(e,-1,stage,s ? s : "loader failure without detail");}
void cairn_math_error_release(CairnMathError *e) {free(e->message);memset(e,0,sizeof(*e));}

/* Read the kernel's file identity for the actual mapped address, not a dladdr path. */
static int mapping(void *address,uint64_t *device,uint64_t *inode,CairnMathError *error) {
    FILE *file=fopen("/proc/self/maps","re");if (!file) return system_error(error,"open maps");
    char *line=NULL;size_t capacity=0;ssize_t length;int found=0,result=0;uintptr_t target=(uintptr_t)address;
    errno=0;
    while ((length=getline(&line,&capacity,file))>=0) {
        uintptr_t begin,end;uint64_t offset,ino;unsigned ma,mi;char permissions[5];
        if (sscanf(line,"%" SCNxPTR "-%" SCNxPTR " %4s %" SCNx64 " %x:%x %" SCNu64,&begin,&end,permissions,&offset,&ma,&mi,&ino)==7 && target>=begin && target<end) {
            *device=(uint64_t)makedev(ma,mi);*inode=ino;found=1;break;
        }
    }
    if (length<0 && !feof(file)) result=system_error(error,"read maps");
    free(line);if (fclose(file)) result=system_error(error,"close maps");
    if (!found) result=cairn_math_error(error,-1,"mapped address","no file mapping for native object");
    return result;
}
static int fd_matches(void *address,int fd,CairnMathError *error) {
    struct stat st;uint64_t dev,ino;
    if (fstat(fd,&st)) return system_error(error,"fstat sealed dependency");
    if (mapping(address,&dev,&ino,error)) return -1;
    if (dev!=(uint64_t)st.st_dev || ino!=(uint64_t)st.st_ino)
        return cairn_math_error(error,-1,"native mapping","resolved symbol is outside the exact selected sealed library");
    return 0;
}

static int sealed_payload(CairnMathBytes input,int *out,CairnMathError *error) {
    int fd=memfd_create("cairn-pinned-math",MFD_CLOEXEC|MFD_ALLOW_SEALING);*out=fd;
    if (fd<0) return system_error(error,"memfd_create math");
    size_t offset=0;
    while (offset<input.length) {
        ssize_t n=write(fd,input.bytes+offset,input.length-offset);
        if (n<0 && errno==EINTR) continue;
        if (n<0) return system_error(error,"write pinned math");
        if (!n) return cairn_math_error(error,-1,"write pinned math","zero-length write");
        offset+=(size_t)n;
    }
    if (fcntl(fd,F_ADD_SEALS,F_SEAL_WRITE|F_SEAL_GROW|F_SEAL_SHRINK|F_SEAL_SEAL)) return system_error(error,"seal pinned math");
    return 0;
}

static int standard_dependency(const char *path) {
    const char *name=strrchr(path,'/');name=name ? name+1 : path;
    static const char *allowed[]={"libm.so.6","libpthread.so.0","libz.so.1","libgcc_s.so.1","libc.so.6","ld-linux-x86-64.so.2"};
    for (size_t i=0;i<sizeof(allowed)/sizeof(allowed[0]);i++) if (!strcmp(name,allowed[i])) return 1;
    return 0;
}
static int collect_modules(CairnMathRuntime *r,CairnMathError *error) {
    struct link_map *map=NULL;
    if (dlinfo(r->handles[0],RTLD_DI_LINKMAP,&map)) return loader_error(error,"dlinfo namespace map");
    while (map && map->l_prev) map=map->l_prev;
    size_t count=0;for (struct link_map *m=map;m;m=m->l_next) if (++count>32) return cairn_math_error(error,-1,"namespace inventory","more than32 native objects");
    r->modules=calloc(count,sizeof(CairnMathModule));if (!r->modules) return cairn_math_error(error,ENOMEM,"namespace inventory","out of memory");
    r->module_count=count;int seen[3]={0,0,0};size_t index=0;
    for (struct link_map *m=map;m;m=m->l_next,index++) {
        CairnMathModule *out=&r->modules[index];out->pinned_index=-1;
        char *path=strdup(m->l_name);if (!path) return cairn_math_error(error,ENOMEM,"copy native path","out of memory");out->path=path;
        if (mapping((void *)(uintptr_t)m->l_addr,&out->device,&out->inode,error)) return -1;
        for (int i=0;i<3;i++) {
            struct stat st;if (fstat(r->fds[i],&st)) return system_error(error,"stat pinned module");
            if (out->device==(uint64_t)st.st_dev && out->inode==(uint64_t)st.st_ino) {out->pinned_index=i;seen[i]++;break;}
        }
        if (out->pinned_index<0 && !standard_dependency(path)) return cairn_math_error(error,-1,"unaccounted namespace dependency",path);
    }
    for (int i=0;i<3;i++) if (seen[i]!=1) return cairn_math_error(error,-1,"pinned dependency inventory","selected BLAS/gfortran/quadmath must each map exactly once");
    return 0;
}

int cairn_math_close(CairnMathRuntime *r,CairnMathError *error) {
    if (!r) return 0;int result=0;
    /* Root before its Fortran and quadmath prerequisites; keep all fds until handles close. */
    for (int i=0;i<3;i++) if (r->handles[i] && dlclose(r->handles[i])) result=loader_error(error,"dlclose math dependency");
    for (int i=0;i<3;i++) if (r->fds[i]>=0 && close(r->fds[i])) result=system_error(error,"close math descriptor");
    for (size_t i=0;i<r->module_count;i++) free((void *)r->modules[i].path);
    free(r->modules);free(r->config);free(r->core);free(r);return result;
}

#define BIND(field,name) do {     dlerror();void *symbol=dlsym(r->handles[0],#name);const char *why=dlerror();     if (why || !symbol) {cairn_math_error(error,-1,#name,why ? why : "null symbol");goto done;}     _Static_assert(sizeof(r->field)==sizeof(symbol),"POSIX function pointer representation");     if (fd_matches(symbol,r->fds[0],error)) goto done;     memcpy(&r->field,&symbol,sizeof(symbol)); } while (0)

int cairn_math_open(const CairnMathBytes inputs[3],CairnMathRuntime **out,CairnMathSettings *settings,CairnMathError *error) {
    *out=NULL;int result=-1;CairnMathRuntime *r=calloc(1,sizeof(*r));
    if (!r) return cairn_math_error(error,ENOMEM,"math runtime","out of memory");
    for (int i=0;i<3;i++) r->fds[i]=-1;
    if (getenv("LD_LIBRARY_PATH") || getenv("LD_PRELOAD") || getenv("LD_AUDIT")) {cairn_math_error(error,-1,"math launcher environment","loader search/preload/audit overrides must be absent from process launch");goto done;}
    for (int i=0;i<3;i++) if (sealed_payload(inputs[i],&r->fds[i],error)) goto done;
    Lmid_t namespace_id=LM_ID_NEWLM;
    for (int i=2;i>=0;i--) {
        char path[64];int n=snprintf(path,sizeof(path),"/proc/self/fd/%d",r->fds[i]);
        if (n<0 || (size_t)n>=sizeof(path)) {cairn_math_error(error,-1,"sealed dependency path","path overflow");goto done;}
        dlerror();r->handles[i]=dlmopen(namespace_id,path,RTLD_NOW|RTLD_LOCAL);
        if (!r->handles[i]) {loader_error(error,"dlmopen selected math dependency");goto done;}
        if (i==2 && dlinfo(r->handles[i],RTLD_DI_LMID,&namespace_id)) {loader_error(error,"dlinfo new math namespace");goto done;}
    }
    if (collect_modules(r,error)) goto done;
    BIND(gemv,scipy_cblas_sgemv64_);BIND(dot,scipy_cblas_sdot64_);
    CairnConfig config;CairnCore core;CairnThreads threads;CairnParallel parallel;
    _Static_assert(sizeof(CairnConfig)==sizeof(void *) && sizeof(CairnCore)==sizeof(void *) && sizeof(CairnThreads)==sizeof(void *) && sizeof(CairnParallel)==sizeof(void *),"header-derived getter pointer ABI");
    {
        dlerror();void *symbol=dlsym(r->handles[0],"scipy_openblas_get_config64_");const char *why=dlerror();
        if (why || !symbol) {cairn_math_error(error,-1,"get_config",why ? why : "null symbol");goto done;}
        if (fd_matches(symbol,r->fds[0],error)) goto done;memcpy(&config,&symbol,sizeof(config));
    }
    {
        dlerror();void *symbol=dlsym(r->handles[0],"scipy_openblas_get_corename64_");const char *why=dlerror();
        if (why || !symbol) {cairn_math_error(error,-1,"get_corename",why ? why : "null symbol");goto done;}
        if (fd_matches(symbol,r->fds[0],error)) goto done;memcpy(&core,&symbol,sizeof(core));
    }
    {
        dlerror();void *symbol=dlsym(r->handles[0],"scipy_openblas_get_num_threads64_");const char *why=dlerror();
        if (why || !symbol) {cairn_math_error(error,-1,"get_num_threads",why ? why : "null symbol");goto done;}
        if (fd_matches(symbol,r->fds[0],error)) goto done;memcpy(&threads,&symbol,sizeof(threads));
    }
    {
        dlerror();void *symbol=dlsym(r->handles[0],"scipy_openblas_get_parallel64_");const char *why=dlerror();
        if (why || !symbol) {cairn_math_error(error,-1,"get_parallel",why ? why : "null symbol");goto done;}
        if (fd_matches(symbol,r->fds[0],error)) goto done;memcpy(&parallel,&symbol,sizeof(parallel));
    }
    char *config_text=config(),*core_text=core();
    if (!config_text || !core_text) {cairn_math_error(error,-1,"native math getters","null config/core string");goto done;}
    r->config=strdup(config_text);r->core=strdup(core_text);
    if (!r->config || !r->core) {cairn_math_error(error,ENOMEM,"copy math settings","out of memory");goto done;}
    settings->config=r->config;settings->core=r->core;settings->threads=threads();settings->parallel=parallel();
    *out=r;r=NULL;result=0;
done:
    if (r) cairn_math_close(r,error);
    return result;
}
size_t cairn_math_module_count(const CairnMathRuntime *r) {return r->module_count;}
int cairn_math_module(const CairnMathRuntime *r,size_t index,CairnMathModule *out,CairnMathError *error) {
    if (index>=r->module_count) return cairn_math_error(error,-1,"module index","out of bounds");
    *out=r->modules[index];return 0;
}
