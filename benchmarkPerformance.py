#!/usr/bin/env python3
import Tee_Logger
import time

def benchmark(tl):
	startTime = time.monotonic_ns()
	tl.teeprint("Starting benchmarkPerformance.py")
	for i in range(1000000):
		tl.info(f"This is a test message: {i}")
	endTime = time.monotonic_ns()
	tl.teeprint("Finished benchmarkPerformance.py")
	elapsedTime = endTime - startTime
	tl.teeprint(f"Elapsed time: {elapsedTime / 1_000_000_000:.2f} seconds")

tl = Tee_Logger.teeLogger(programName="no_compression")
benchmark(tl)
tl.teeprint("Finished benchmarkPerformance.py with no compression")

tl = Tee_Logger.teeLogger(in_place_compression='gzip',programName="gzip")
benchmark(tl)
tl.teeprint("Finished benchmarkPerformance.py with gzip compression")

tl = Tee_Logger.teeLogger(in_place_compression='bz2',programName="bz2")
benchmark(tl)
tl.teeprint("Finished benchmarkPerformance.py with bz2 compression")

tl = Tee_Logger.teeLogger(in_place_compression='xz',programName="xz")
benchmark(tl)
tl.teeprint("Finished benchmarkPerformance.py with xz compression")


