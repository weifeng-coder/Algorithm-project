/* seqlist.h —— 实验内容 1：存放待排序数据的数据结构（顺序表）
 * 双语核心：可同时被 C（-std=c11）与 C++（-std=c++17）编译。
 */
#ifndef SEQLIST_H
#define SEQLIST_H

#include <stdlib.h>

typedef struct SeqList {
    int *data;      /* 动态数组首地址 */
    int   length;   /* 当前元素个数 */
    int   capacity; /* 已分配容量 */
} SeqList;

static SeqList *SeqList_Create(int capacity) {
    SeqList *L = (SeqList *)malloc(sizeof(SeqList));
    if (!L) return NULL;
    if (capacity < 1) capacity = 1;
    L->data = (int *)malloc(sizeof(int) * (size_t)capacity);
    L->length = 0;
    L->capacity = capacity;
    return L;
}

static void SeqList_Free(SeqList *L) {
    if (!L) return;
    free(L->data);
    free(L);
}

/* 扩容：只增不减，缩容请求被忽略 */
static void SeqList_Resize(SeqList *L, int new_capacity) {
    if (!L || new_capacity <= L->capacity) return;
    int *p = (int *)realloc(L->data, sizeof(int) * (size_t)new_capacity);
    if (!p) return;
    L->data = p;
    L->capacity = new_capacity;
}

#endif /* SEQLIST_H */
