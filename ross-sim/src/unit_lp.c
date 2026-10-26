#include "mil_sim.h"
#include <stdlib.h>

static void schedule_move(unit_state *s, tw_lp *lp, tw_stime delay)
{
    tw_stime ts = tw_rand_exponential(lp->rng, g_mean_interval);

    s->last_move = tw_now(lp) + delay + ts;

    tw_event *e = tw_event_new(lp->gid, delay + ts, lp);
    mil_message *m = tw_event_data(e);
    memset(m, 0, sizeof(*m));
    m->type = EVENT_MOVE;
    m->unit_type = s->type;
    m->side = s->side;
    tw_event_send(e);
}

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
    s->comm_node = g_comm_lp;

    schedule_move(s, lp, 0.0);
}

void event_unit_lp(unit_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    (void) bf;

    switch (msg->type) {
        case EVENT_MOVE:
            if (s->health > 0) {
                s->x += (s->side == 0) ? s->speed * 0.1 : -s->speed * 0.05;
                s->y += tw_rand_unif(lp->rng) * 2 - 1;

                /* 机动后把目标信息上报给传感器 LP */
                tw_event *se = tw_event_new(g_sensor_lp, 0.1, lp);
                mil_message *sm = tw_event_data(se);
                memset(sm, 0, sizeof(*sm));
                sm->type = EVENT_SENSOR_UPDATE;
                sm->target_id = (int) lp->gid;
                sm->side = s->side;
                sm->x = s->x;
                sm->y = s->y;
                tw_event_send(se);

                /* 有一定概率进入交战流程 */
                if (tw_rand_unif(lp->rng) < 0.3 && s->ammo > 0) {
                    tw_event *ee = tw_event_new(lp->gid, 0.5, lp);
                    mil_message *em = tw_event_data(ee);
                    memset(em, 0, sizeof(*em));
                    em->type = EVENT_ENGAGE;
                    em->target_id = (int) ((lp->gid + 1) % num_units);
                    tw_event_send(ee);
                }
            }
            schedule_move(s, lp, 0.0);
            break;

        case EVENT_ENGAGE:
            if (s->ammo > 0 && s->health > 0) {
                s->ammo--;
                tw_event *e = tw_event_new(g_fc_lp, 0.01, lp);
                mil_message *m = tw_event_data(e);
                memset(m, 0, sizeof(*m));
                m->type = EVENT_FIRE_MISSION;
                m->unit_type = s->type;
                m->target_id = msg->target_id;
                tw_event_send(e);
            }
            break;

        case EVENT_DETECT:
            s->detected = 1;
            break;

        case EVENT_DAMAGE:
            s->health -= msg->damage;
            if (s->health <= 0) {
                s->health = 0;
                printf("LP %llu: UNIT DESTROYED at time %.2f\n",
                       (unsigned long long) lp->gid, tw_now(lp));
            }
            break;

        case EVENT_FIRE:
            if (msg->target_id == (int) lp->gid && s->health > 0) {
                s->health -= msg->damage;
            }
            break;

        default:
            schedule_move(s, lp, 0.0);
            break;
    }
}

void rc_event_unit_lp(unit_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    /* 顺序仿真（--synch=1）下不会触发回滚 */
    (void) s;
    (void) bf;
    (void) msg;
    (void) lp;
}

void final_unit_lp(unit_state *s, tw_lp *lp)
{
    if (s->health > 0) {
        printf("LP %llu (side %d): Final status OK, health=%d, ammo=%d, pos=(%.1f,%.1f)\n",
               (unsigned long long) lp->gid, s->side, s->health, s->ammo, s->x, s->y);
    }
}
