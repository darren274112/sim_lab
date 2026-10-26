#include "mil_sim.h"

static void schedule_comm(comm_state *s, tw_lp *lp)
{
    tw_stime ts = tw_rand_exponential(lp->rng, g_mean_interval);

    (void) s;
    tw_event *e = tw_event_new(lp->gid, ts, lp);
    mil_message *m = tw_event_data(e);
    memset(m, 0, sizeof(*m));
    m->type = EVENT_COMM_MSG;
    tw_event_send(e);
}

void init_comm_lp(comm_state *s, tw_lp *lp)
{
    s->id = lp->gid;
    s->comms_up = 1;
    s->delay = 0.001;
    s->msg_count = 0;

    printf("Comm LP %llu initialized\n", (unsigned long long) lp->gid);
    schedule_comm(s, lp);
}

void event_comm_lp(comm_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    (void) bf;

    switch (msg->type) {
        case EVENT_COMM_LOST:
            s->comms_up = 0;
            s->delay = 1.0;
            printf("Comm LP %llu: COMMUNICATIONS LOST at time %.2f\n",
                   (unsigned long long) lp->gid, tw_now(lp));
            return;

        case EVENT_COMM_RESTORED:
            s->comms_up = 1;
            s->delay = 0.001;
            printf("Comm LP %llu: COMMUNICATIONS RESTORED at time %.2f\n",
                   (unsigned long long) lp->gid, tw_now(lp));
            return;

        case EVENT_COMM_MSG:
            if (s->comms_up) {
                s->msg_count++;
            }
            /* 通信中断时消息直接丢弃，但仍继续产生后续消息 */
            schedule_comm(s, lp);
            return;

        default:
            return;
    }
}

void rc_event_comm_lp(comm_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    (void) s;
    (void) bf;
    (void) msg;
    (void) lp;
}

void final_comm_lp(comm_state *s, tw_lp *lp)
{
    printf("Comm LP %llu: Messages=%d, Status=%s\n",
           (unsigned long long) lp->gid, s->msg_count,
           s->comms_up ? "UP" : "DOWN");
}
