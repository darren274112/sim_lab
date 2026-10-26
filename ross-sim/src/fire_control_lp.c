#include "mil_sim.h"

void init_fc_lp(fc_state *s, tw_lp *lp)
{
    s->id = lp->gid;
    s->missions_active = 0;
    s->rounds_fired = 0;
    s->degraded = 0;
    s->efficiency = 1.0;

    printf("Fire Control LP %llu initialized\n",
           (unsigned long long) lp->gid);
}

void event_fc_lp(fc_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    (void) bf;

    /* 故障注入后火控效率下降 */
    if (s->degraded) {
        s->efficiency = 0.3;
    }

    if (msg->type == EVENT_FIRE_MISSION ||
        strcmp(msg->msg, "TRACK_UPDATE") == 0) {
        s->missions_active++;
        s->rounds_fired += (int) (3 * s->efficiency);

        /* 避免每个事件都打印，只输出周期性汇总 */
        if (s->missions_active % 50 == 0) {
            printf("FC LP %llu: %d fire missions, %d rounds at time %.2f "
                   "(efficiency=%.2f)\n",
                   (unsigned long long) lp->gid, s->missions_active,
                   s->rounds_fired, tw_now(lp), s->efficiency);
        }
    }
}

void rc_event_fc_lp(fc_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    (void) s;
    (void) bf;
    (void) msg;
    (void) lp;
}

void final_fc_lp(fc_state *s, tw_lp *lp)
{
    printf("Fire Control LP %llu: Missions=%d, Rounds fired=%d\n",
           (unsigned long long) lp->gid, s->missions_active, s->rounds_fired);
}
