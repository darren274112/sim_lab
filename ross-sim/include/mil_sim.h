#ifndef MIL_SIM_H
#define MIL_SIM_H

#include <ross.h>

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

#endif
