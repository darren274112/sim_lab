#include "mil_sim.h"
#include <stdio.h>

tw_lpid *unit_lps = NULL;
int num_units = 20;
tw_stime g_mean_interval = 1.0;

tw_lpid g_fc_lp = 0;
tw_lpid g_sensor_lp = 0;
tw_lpid g_comm_lp = 0;
tw_lpid g_terrain_lp = 0;

void init_unit_lp(unit_state *s, tw_lp *lp);
void event_unit_lp(unit_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void rc_event_unit_lp(unit_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void final_unit_lp(unit_state *s, tw_lp *lp);

void init_sensor_lp(sensor_state *s, tw_lp *lp);
void event_sensor_lp(sensor_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void rc_event_sensor_lp(sensor_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void final_sensor_lp(sensor_state *s, tw_lp *lp);

void init_fc_lp(fc_state *s, tw_lp *lp);
void event_fc_lp(fc_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void rc_event_fc_lp(fc_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void final_fc_lp(fc_state *s, tw_lp *lp);

void init_comm_lp(comm_state *s, tw_lp *lp);
void event_comm_lp(comm_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void rc_event_comm_lp(comm_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void final_comm_lp(comm_state *s, tw_lp *lp);

void init_terrain_lp(terrain_state *s, tw_lp *lp);
void event_terrain_lp(terrain_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void rc_event_terrain_lp(terrain_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp);
void final_terrain_lp(terrain_state *s, tw_lp *lp);

static tw_peid mil_map(tw_lpid gid)
{
    return (tw_peid) (gid / g_tw_nlp);
}

/* Simplified ROSS 的 tw_lptype 有 8 个字段：
 * init, pre_run, event, revent, commit, final, map, state_sz */
tw_lptype military_lps[] = {
    {
        (init_f) init_unit_lp,
        (pre_run_f) NULL,
        (event_f) event_unit_lp,
        (revent_f) rc_event_unit_lp,
        (commit_f) NULL,
        (final_f) final_unit_lp,
        (map_f) mil_map,
        sizeof(unit_state)
    },
    {
        (init_f) init_sensor_lp,
        (pre_run_f) NULL,
        (event_f) event_sensor_lp,
        (revent_f) rc_event_sensor_lp,
        (commit_f) NULL,
        (final_f) final_sensor_lp,
        (map_f) mil_map,
        sizeof(sensor_state)
    },
    {
        (init_f) init_fc_lp,
        (pre_run_f) NULL,
        (event_f) event_fc_lp,
        (revent_f) rc_event_fc_lp,
        (commit_f) NULL,
        (final_f) final_fc_lp,
        (map_f) mil_map,
        sizeof(fc_state)
    },
    {
        (init_f) init_comm_lp,
        (pre_run_f) NULL,
        (event_f) event_comm_lp,
        (revent_f) rc_event_comm_lp,
        (commit_f) NULL,
        (final_f) final_comm_lp,
        (map_f) mil_map,
        sizeof(comm_state)
    },
    {
        (init_f) init_terrain_lp,
        (pre_run_f) NULL,
        (event_f) event_terrain_lp,
        (revent_f) rc_event_terrain_lp,
        (commit_f) NULL,
        (final_f) final_terrain_lp,
        (map_f) mil_map,
        sizeof(terrain_state)
    },
    {0}
};

static unsigned int cli_units = 20;

const tw_optdef app_opt[] = {
    TWOPT_GROUP("Military Simulation Options"),
    TWOPT_UINT("units", cli_units, "number of unit LPs (default 20)"),
    TWOPT_STIME("mean", g_mean_interval, "mean interval between unit events"),
    TWOPT_END()
};

int main(int argc, char **argv)
{
    tw_opt_add(app_opt);
    tw_init(&argc, &argv);

    if (cli_units < 2) {
        cli_units = 2;
    }
    if (cli_units > MAX_UNITS) {
        cli_units = MAX_UNITS;
    }
    num_units = (int) cli_units;

    fault_init();

    /* 单位 LP + 火控/传感器/通信/地形 4 个特殊 LP */
    g_fc_lp = num_units;
    g_sensor_lp = num_units + 1;
    g_comm_lp = num_units + 2;
    g_terrain_lp = num_units + 3;

    printf("=== ROSS Military Simulation: Battalion Attack ===\n");
    printf("Blue Force: %d units | Red Force: %d units\n",
           (num_units + 1) / 2, num_units / 2);
    printf("LPs: %d units + 4 support LPs\n\n", num_units);

    tw_define_lps(num_units + 4, sizeof(mil_message));

    for (int i = 0; i < num_units; i++) {
        tw_lp_settype(i, &military_lps[0]);
    }
    tw_lp_settype(g_fc_lp, &military_lps[2]);
    tw_lp_settype(g_sensor_lp, &military_lps[1]);
    tw_lp_settype(g_comm_lp, &military_lps[3]);
    tw_lp_settype(g_terrain_lp, &military_lps[4]);

    tw_run();

    if (g_fault.enabled) {
        printf("=== FAULT REPORT: type=%s target=%llu at=%.2f, events_dropped=%ld ===\n",
               g_fault.type == FAULT_LP_CRASH ? "lp_crash" :
               (g_fault.type == FAULT_COMM_LOSS ? "comm_loss" : "none"),
               (unsigned long long) g_fault.target, g_fault.at, g_fault.dropped);
    }

    printf("\n=== Simulation Complete (end time %.1f) ===\n", g_tw_ts_end);

    tw_end();
    return 0;
}
