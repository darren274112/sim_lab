#ifndef MIL_SIM_H
#define MIL_SIM_H

#include <ross.h>
#include <string.h>

#define MAX_UNITS 32
#define MAX_TRACKS 64

/* Event types */
typedef enum {
    EVENT_MOVE = 1,
    EVENT_DETECT,
    EVENT_ENGAGE,
    EVENT_FIRE,
    EVENT_DAMAGE,
    EVENT_COMM_MSG,
    EVENT_SENSOR_UPDATE,
    EVENT_FIRE_MISSION,
    EVENT_COMM_LOST,
    EVENT_COMM_RESTORED
} event_t;

/* Unit types */
typedef enum {
    UNIT_TANK,
    UNIT_IFV,
    UNIT_INFANTRY,
    UNIT_ARTILLERY,
    UNIT_AT_TEAM,
    UNIT_FO  /* Forward Observer */
} unit_type_t;

/* LP types */
typedef enum {
    LP_UNIT = 1,
    LP_SENSOR,
    LP_FIRECONTROL,
    LP_COMM,
    LP_TERRAIN
} lp_type_t;

/* Unit LP state */
typedef struct {
    tw_lpid id;
    unit_type_t type;
    int side; /* 0=blue, 1=red */
    double x, y;
    double speed;
    int health;
    int ammo;
    int detected;
    tw_stime last_move;
    tw_lpid comm_node;
} unit_state;

/* Sensor LP state */
typedef struct {
    tw_lpid id;
    double range;
    int tracks[MAX_TRACKS];
    int track_count;
    int active;
} sensor_state;

/* Fire Control LP state */
typedef struct {
    tw_lpid id;
    int missions_active;
    int rounds_fired;
    int degraded; /* set to 1 on fault */
    double efficiency;
} fc_state;

/* Comm LP state */
typedef struct {
    tw_lpid id;
    int comms_up;
    double delay;
    int msg_count;
} comm_state;

/* Terrain LP state */
typedef struct {
    tw_lpid id;
    double visibility;
    int time_of_day;
} terrain_state;

/* Event messages */
typedef struct {
    int type;
    unit_type_t unit_type;
    int side;
    double x, y;
    int target_id;
    int damage;
    int track_id;
    char msg[64];
} mil_message;

extern tw_lpid *unit_lps;
extern int num_units;
extern tw_stime g_mean_interval;

/* 特殊 LP 的 gid：单位 LP 占用 0..num_units-1 */
extern tw_lpid g_fc_lp;
extern tw_lpid g_sensor_lp;
extern tw_lpid g_comm_lp;
extern tw_lpid g_terrain_lp;

/* ===== 故障注入（由 sim-bridge 通过环境变量 ROSS_FAULT_* 传入） ===== */
typedef enum {
    FAULT_NONE = 0,
    FAULT_LP_CRASH,   /* 目标 LP 在 at 时刻后停止处理任何事件（视为宕机） */
    FAULT_COMM_LOSS   /* 通信 LP 在 at 时刻后中断，COMM_MSG 全部丢弃 */
} fault_type_t;

typedef struct {
    int enabled;
    fault_type_t type;
    tw_lpid target;   /* 目标 LP gid */
    tw_stime at;      /* 触发仿真时刻 */
    long dropped;     /* 被故障丢弃的事件计数（用于报告） */
} fault_cfg_t;

extern fault_cfg_t g_fault;

/* 返回 1 表示当前事件应被故障丢弃（目标 LP 已宕机） */
int fault_drop_event(tw_lpid lp_gid, tw_stime now);

/* 从环境变量初始化故障配置；必须在 tw_run() 之前调用 */
void fault_init(void);

#endif
