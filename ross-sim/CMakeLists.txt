cmake_minimum_required(VERSION 3.10)
project(ross_mil_sim C)

# ROSS path - adjust as needed
set(ROSS_DIR /opt/ross CACHE PATH "ROSS installation directory")
include_directories(${ROSS_DIR}/include)
link_directories(${ROSS_DIR}/lib)

add_executable(mil_sim
    src/main.c
    src/unit_lp.c
    src/sensor_lp.c
    src/fire_control_lp.c
    src/comm_lp.c
    src/terrain_lp.c
    src/events.c
)

target_link_libraries(mil_sim ROSS m)
