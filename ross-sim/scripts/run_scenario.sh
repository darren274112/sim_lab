#!/bin/bash
# Run battalion attack scenario
cd /Users/darren/sim/ross-sim/build
./mil_sim --synch=3 --end=100.0 --nlp=24 2>&1 | tail -60
