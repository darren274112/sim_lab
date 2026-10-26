#include "mil_sim.h"

void init_sensor_lp(sensor_state *s, tw_lp *lp)
{
    s->id = lp->gid;
    s->range = 500.0;
    s->track_count = 0;
    s->active = 1;
    
    printf("Sensor LP %lu initialized, range=%.1f\n", lp->gid, s->range);
}

void event_sensor_lp(sensor_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    if (!s->active) return;
    
    /* Detect enemy units */
    if (s->track_count < MAX_TRACKS) {
        s->tracks[s->track_count++] = msg->target_id;
        printf("Sensor LP %lu: DETECTED target at time %f\n", lp->gid, tw_now(lp));
        
        /* Notify fire control */
        tw_lpid fc_lp = 20;
        tw_event *e = tw_event_new(fc_lp, tw_now(lp) + 0.001, lp);
        mil_message *m = tw_event_data(e);
        m->track_id = msg->target_id;
        strcpy(m->msg, "TRACK_UPDATE");
        tw_event_send(e);
    }
}

void rc_event_sensor_lp(sensor_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
}

void final_sensor_lp(sensor_state *s, tw_lp *lp)
{
    printf("Sensor LP %lu: Tracked %d targets\n", lp->gid, s->track_count);
}
