#include "mil_sim.h"
#include <stdlib.h>
#include <string.h>

/* 全局故障配置（由 fault_init 从环境变量填充） */
fault_cfg_t g_fault = {0};

void fault_init(void)
{
    char *e = getenv("ROSS_FAULT_TYPE");
    if (!e || e[0] == '\0')
        return;

    if (strcmp(e, "lp_crash") == 0)
        g_fault.type = FAULT_LP_CRASH;
    else if (strcmp(e, "comm_loss") == 0)
        g_fault.type = FAULT_COMM_LOSS;
    else
        return;  /* 未知类型，忽略 */

    e = getenv("ROSS_FAULT_TARGET");
    if (e)
        g_fault.target = (tw_lpid) strtol(e, NULL, 10);

    e = getenv("ROSS_FAULT_AT");
    if (e)
        g_fault.at = (tw_stime) strtod(e, NULL);

    g_fault.enabled = 1;
    printf("=== FAULT INJECTED: type=%s target=%llu at=%.2f ===\n",
           g_fault.type == FAULT_LP_CRASH ? "lp_crash" : "comm_loss",
           (unsigned long long) g_fault.target, g_fault.at);
}

int fault_drop_event(tw_lpid lp_gid, tw_stime now)
{
    if (!g_fault.enabled)
        return 0;
    if (now < g_fault.at)
        return 0;
    if (g_fault.type == FAULT_LP_CRASH && lp_gid == g_fault.target) {
        g_fault.dropped++;
        return 1;  /* 目标 LP 已宕机，丢弃其所有入站事件 */
    }
    return 0;
}
