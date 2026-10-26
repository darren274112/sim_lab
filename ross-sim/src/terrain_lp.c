#include "mil_sim.h"

void init_terrain_lp(terrain_state *s, tw_lp *lp)
{
    s->id = lp->gid;
    s->visibility = 8000.0;
    s->time_of_day = 12;
    
    printf("Terrain LP %lu initialized\n", lp->gid);
}

void event_terrain_lp(terrain_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    /* Terrain affects visibility and movement */
}

void rc_event_terrain_lp(terrain_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
}

void final_terrain_lp(terrain_state *s, tw_lp *lp)
{
    printf("Terrain LP %lu: Visibility=%.0fm\n", lp->gid, s->visibility);
}
