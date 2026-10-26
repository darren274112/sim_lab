#include "mil_sim.h"
#include <stdio.h>

tw_lpid *unit_lps = NULL;
int num_units = 20;
tw_stime g_mean_interval = 1.0;

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

tw_lptype military_lps[] = {
    {
        (init_f) init_unit_lp,
        (event_f) event_unit_lp,
        (revent_f) rc_event_unit_lp,
        (final_f) final_unit_lp,
        (map_f) NULL,
        sizeof(unit_state)
    },
    {
        (init_f) init_sensor_lp,
        (event_f) event_sensor_lp,
        (revent_f) rc_event_sensor_lp,
        (final_f) final_sensor_lp,
        (map_f) NULL,
        sizeof(sensor_state)
    },
    {
        (init_f) init_fc_lp,
        (event_f) event_fc_lp,
        (revent_f) rc_event_fc_lp,
        (final_f) final_fc_lp,
        (map_f) NULL,
        sizeof(fc_state)
    },
    {
        (init_f) init_comm_lp,
        (event_f) event_comm_lp,
        (revent_f) rc_event_comm_lp,
        (final_f) final_comm_lp,
        (map_f) NULL,
        sizeof(comm_state)
    },
    {
        (init_f) init_terrain_lp,
        (event_f) event_terrain_lp,
        (revent_f) rc_event_terrain_lp,
        (final_f) final_terrain_lp,
        (map_f) NULL,
        sizeof(terrain_state)
    },
    {NULL, NULL, NULL, NULL, NULL, 0}
};

int main(int argc, char **argv)
{
    tw_opt_add(app_opt);
    tw_init(&argc, &argv);
    
    printf("=== ROSS Military Simulation: Battalion Attack ===\n");
    printf("Blue Force: 12 units | Red Force: 8 units\n");
    printf("Simulation Start (Logical Time)\n\n");
    
    tw_define_lps(num_units + 4, sizeof(mil_message));
    tw_set_lps_per_pe(0);
    
    for (int i = 0; i < tw_nlp; i++)
        tw_lp_settype(i, &military_lps[i % 5]);
    
    tw_run();
    
    printf("\n=== Simulation Complete ===\n");
    printf("Final GVT: %f\n", tw_now(lp));
    
    tw_end();
    return 0;
}

const tw_optdef app_opt[] = {
    TWOPT_GROUP("Military Simulation Options"),
    TWOPT_END()
};
