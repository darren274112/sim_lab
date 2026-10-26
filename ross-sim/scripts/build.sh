#!/bin/bash
cd /Users/darren/sim/ross-sim/build
cmake .. -DROSS_DIR=/opt/ross 2>&1 | tail -20
make 2>&1 | tail -30
