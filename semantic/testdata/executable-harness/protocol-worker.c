/* Compiled process fixture only. It implements no embedding or Rust model. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef MODE
#define MODE 1
#endif

#ifndef MODEL
#define MODEL "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
#endif
#ifndef SCORE
#define SCORE 42
#endif
#ifndef BODY_HASH
#define BODY_HASH "230d8358dc8e8890b4c58deeb62912ee2f20357ae2a5cc861b98e68fe31acb5"
#endif
#ifndef QUERY_HASH
#define QUERY_HASH "ignored"
#endif
#ifndef QUERY_Y
#define QUERY_Y 0
#endif
#ifndef DOCUMENT_X
#define DOCUMENT_X 0
#endif
#ifndef VERSION
#define VERSION 1
#endif
#ifndef SPAN_LENGTH
#define SPAN_LENGTH 4
#endif
#ifndef TOKENS
#define TOKENS 3
#endif

static int field(const char *line, const char *key, char *out, size_t size) {
    char pattern[80];
    snprintf(pattern,sizeof(pattern),"\"%s\":\"",key);
    const char *start=strstr(line,pattern);
    if (!start) return 0;
    start+=strlen(pattern);
    const char *end=strchr(start,'"');
    if (!end || (size_t)(end-start)>=size) return 0;
    memcpy(out,start,(size_t)(end-start));out[end-start]=0;
    return 1;
}

static void rank_result(void) {
    printf("{\"model_sha256\":\"%s\",\"algorithm\":\"bge-max-chunk/1\","
           "\"scores\":[{\"record_id\":\"fixture\",\"version\":%d,"
           "\"body_sha256\":\"%s\",\"score\":%d}]}",MODEL,VERSION,BODY_HASH,SCORE);
}

int main(int argc, char **argv) {
    (void)argv;
    if (argc != 1) return 91;
    if (MODE==1) {
        while (getchar()!=EOF) {}
        rank_result();puts("");return 0;
    }
    char *line=NULL;size_t size=0;unsigned calls=0;
    while (getline(&line,&size,stdin)>0) {
        char id[80],operation[80];
        if (!field(line,"id",id,sizeof(id))) return 2;
        if (strstr(line,"\"query\":\"refuse\"")) {fputs("fixture refused\n",stderr);return 7;}
        if (MODE==2) {
            if (strstr(line,"\"cache\":") && calls) return 8;
            printf("{\"id\":\"%s\",\"result\":",id);rank_result();
            printf(",\"cache\":{\"format\":\"cairn.passage-vectors/1\",\"model_sha256\":\"%s\",\"vectors\":{}}}\n",MODEL);
        } else {
            if (!field(line,"operation",operation,sizeof(operation))) return 2;
            printf("{\"id\":\"%s\",\"identity\":{\"model_sha256\":\"%s\",\"algorithm\":\"bge-original-passages/1\",\"dimensions\":384}",id,MODEL);
            if (!strcmp(operation,"document")) printf(",\"passages\":[{\"span\":{\"offset\":0,\"length\":%d},\"vector\":[",SPAN_LENGTH);
            else if (!strcmp(operation,"query")) printf(",\"vector\":[");
            if (strcmp(operation,"identity")) {
                for (int n=0;n<384;n++) {
                    double component = !strcmp(operation,"query") ?
                        (n==0?1:(n==1?QUERY_Y:0)) : (n==0?DOCUMENT_X:(n==1?1:0));
                    printf("%s%.9g",n?",":"",component);
                }
                printf("]");
                if (!strcmp(operation,"document")) printf("}]");
                else printf(",\"query_projection\":{\"method\":\"original-prefix/1\",\"truncated\":false,\"original_tokens\":%d,\"embedded_tokens\":%d,\"prefix_bytes\":5,\"prefix_sha256\":\"%s\"}",TOKENS,TOKENS,QUERY_HASH);
            }
            puts("}");
        }
        fflush(stdout);calls++;
    }
    free(line);
    return 0;
}
