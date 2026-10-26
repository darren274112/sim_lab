#include "mil_sim.h"
#include <stdlib.h>

void init_unit_lp(unit_state *s, tw_lp *lp)
{
    s->id = lp->gid;
    s->side = lp->gid % 2;
    s->type = lp->gid % 6;
    s->x = (lp->gid % 2 == 0) ? 100.0 + (lp->gid * 10) : 500.0 + (lp->gid * 5);
    s->y = 200.0;
    s->speed = 10.0;
    s->health = 100;
    s->ammo = 50;
    s->detected = 0;
    s->comm_node = 0;
    
    if (tw_now(lp) == 0) {
        tw_event *e = tw_event_new(lp->gid, tw_rand_exponential(lp->rng, g_mean_interval), lp);
        mil_message *m = tw_event_data(e);
        m->unit_type = s->type;
        m->side = s->side;
        m->x = s->x;
        m->y = s->y;
        tw_event_send(e);
    }
}

void event_unit_lp(unit_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    tw_stime ts = tw_rand_exponential(lp->rng, g_mean_interval);
    
    switch (tw_event_type(lp)) {
        case EVENT_MOVE:
            if (s->health > 0) {
                s->x += (s->side == 0) ? s->speed * 0.1 : -s->speed * 0.05;
                s->y += tw_rand_uniform(lp->rng) * 2 - 1;
                /* Schedule next move */
                tw_event *e = tw_event_new(lp->gid, tw_now(lp) + ts, lp);
                tw_event_data(e);
                tw_event_send(e);
            }
            break;
        case EVENT_DETECT:
            s->detected = 1;
            break;
        case EVENT_ENGAGE:
            if (s->ammo > 0 && s->health > 0) {
                s->ammo--;
                /* Send fire event to fire control */
                tw_lpid fc_lp = 20; /* Fire control LP */
                tw_event *e = tw_event_new(fc_lp, tw_now(lp) + 0.01, lp);
                mil_message *m = tw_event_data(e);
                m->unit_type = s->type;
                m->target_id = msg->target_id;
                tw_event_send(e);
            }
            break;
        case EVENT_DAMAGE:
            s->health -= msg->damage;
            if (s->health <= 0) {
                s->health = 0;
                printf("LP %lu: UNIT DESTROYED at time %f\n", lp->gid, tw_now(lp));
            }
            break;
        default:
            /* Schedule move event */
            {
                tw_event *e = tw_event_new(lp->gid, tw_now(lp) + ts, lp);
                tw_event_data(e);
                tw_event_send(e);
            }
            break;
    }
}

void rc_event_unit_lp(unit_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    /* Reverse computation for rollback */
}

void final_unit_lp(unit_state *s, tw_lp *lp)
{
    if (s->health > 0)
        printf("LP %lu (side %d): Final status OK, health=%d, pos=(%.1f,%.1f)\n", 
               lp->gid, s->side, s->health, s->x, s->y);
}
