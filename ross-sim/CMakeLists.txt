cmake_minimum_required(VERSION 3.16)
project(ross_mil_sim C)

# ROSS 安装路径（默认 /opt/ross，可用 -DROSS_DIR=... 覆盖）
set(ROSS_DIR /opt/ross CACHE PATH "ROSS installation directory")
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/include)
include_directories(${ROSS_DIR}/include)
include_directories(${ROSS_DIR}/include/ross)
link_directories(${ROSS_DIR}/lib)

# ROSS 静态库依赖 MPI，即使单进程运行也需要链接 MPI
find_package(MPI REQUIRED COMPONENTS C)

add_executable(mil_sim
    src/main.c
    src/unit_lp.c
    src/sensor_lp.c
    src/fire_control_lp.c
    src/comm_lp.c
    src/terrain_lp.c
    src/events.c
)

target_link_libraries(mil_sim ROSS m MPI::MPI_C)
