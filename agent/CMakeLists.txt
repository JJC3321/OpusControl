cmake_minimum_required(VERSION 3.14)
project(opus_control_monitor LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)

find_package(PkgConfig REQUIRED)
pkg_check_modules(HIREDIS REQUIRED hiredis)

add_executable(monitor src/monitor.cpp)
target_include_directories(monitor PRIVATE ${HIREDIS_INCLUDE_DIRS})
target_link_libraries(monitor ${HIREDIS_LIBRARIES})
