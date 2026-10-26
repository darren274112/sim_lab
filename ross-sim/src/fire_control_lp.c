#include "mil_sim.h"

void init_fc_lp(fc_state *s, tw_lp *lp)
{
    s->id = lp->gid;
    s->missions_active = 0;
    s->rounds_fired = 0;
    s->degraded = 0;
    s->efficiency = 1.0;
    
    printf("Fire Control LP %lu initialized\n", lp->gid);
}

void event_fc_lp(fc_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    /* Check if degraded due to fault */
    if (s->degraded) {
        s->efficiency = 0.3; /* 70% degradation */
    }
    
    if (tw_event_type(lp) == EVENT_FIRE_MISSION || strcmp(msg->msg, "TRACK_UPDATE") == 0) {
        if (s->missions_active < 5) {
            s->missions_active++;
            s->rounds_fired += (int)(3 * s->efficiency);
            printf("FC LP %lu: FIRE MISSION #%d at time %f (efficiency=%.2f)\n", 
                   lp->gid, s->missions_active, tw_now(lp), s->efficiency);
        }
    }
}

void rc_event_fc_lp(fc_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
}

void final_fc_lp(fc_state *s, tw_lp *lp)
{
    printf("Fire Control LP %lu: Missions=%d, Rounds fired=%d\n", 
           lp->gid, s->missions_active, s->rounds_fired);
}
