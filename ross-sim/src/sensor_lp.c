#include "mil_sim.h"

void init_sensor_lp(sensor_state *s, tw_lp *lp)
{
    s->id = lp->gid;
    s->range = 500.0;
    s->track_count = 0;
    s->active = 1;

    printf("Sensor LP %llu initialized, range=%.1f\n",
           (unsigned long long) lp->gid, s->range);
}

void event_sensor_lp(sensor_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    (void) bf;

    if (!s->active) {
        return;
    }

    if (msg->type != EVENT_SENSOR_UPDATE) {
        return;
    }

    /* 记录目标并通知火控 */
    if (s->track_count < MAX_TRACKS) {
        s->tracks[s->track_count++] = msg->target_id;
    }

    tw_event *e = tw_event_new(g_fc_lp, 0.001, lp);
    mil_message *m = tw_event_data(e);
    memset(m, 0, sizeof(*m));
    m->type = EVENT_FIRE_MISSION;
    m->track_id = msg->target_id;
    m->side = msg->side;
    strcpy(m->msg, "TRACK_UPDATE");
    tw_event_send(e);
}

void rc_event_sensor_lp(sensor_state *s, tw_bf *bf, mil_message *msg, tw_lp *lp)
{
    (void) s;
    (void) bf;
    (void) msg;
    (void) lp;
}

void final_sensor_lp(sensor_state *s, tw_lp *lp)
{
    printf("Sensor LP %llu: Tracked %d target reports\n",
           (unsigned long long) lp->gid, s->track_count);
}
