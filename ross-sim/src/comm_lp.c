#include "mil_sim.h"

void init_comm_lp(comm_state *s, tw_lp *lp)
{
    s->id = lp->gid;
    s->comms_up = 1;
    s->delay = 0.001;
    s->msg_count = 0;
    
    printf("Comm LP %lu initialized\n", lp->gid);
}

void event_comm_lp(comm_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    if (tw_event_type(lp) == EVENT_COMM_LOST) {
        s->comms_up = 0;
        s->delay = 1.0;
        printf("Comm LP %lu: COMMUNICATIONS LOST at time %f\n", lp->gid, tw_now(lp));
        return;
    }
    
    if (tw_event_type(lp) == EVENT_COMM_RESTORED) {
        s->comms_up = 1;
        s->delay = 0.001;
        printf("Comm LP %lu: COMMUNICATIONS RESTORED at time %f\n", lp->gid, tw_now(lp));
        return;
    }
    
    if (s->comms_up) {
        s->msg_count++;
    } else {
        /* Messages dropped when comms down */
    }
}

void rc_event_comm_lp(comm_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
}

void final_comm_lp(comm_state *s, tw_lp *lp)
{
    printf("Comm LP %lu: Messages=%d, Status=%s\n", lp->gid, s->msg_count, 
           s->comms_up ? "UP" : "DOWN");
}
