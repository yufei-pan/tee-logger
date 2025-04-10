#!/usr/bin/env python3
import Tee_Logger
import Tee_Logger as Tee_Logger_Bin
import time
import re

try:
	import resource
	RESOURCE_LIB_AVAILABLE = True
except ImportError:
	RESOURCE_LIB_AVAILABLE = False

def format_bytes(size, use_1024_bytes=None, to_int=False, to_str=False,str_format='.2f'):
	"""
	Format the size in bytes to a human-readable format or vice versa.
	From hpcp: https://github.com/yufei-pan/hpcp

	Args:
		size (int or str): The size in bytes or a string representation of the size.
		use_1024_bytes (bool, optional): Whether to use 1024 bytes as the base for conversion. If None, it will be determined automatically. Default is None.
		to_int (bool, optional): Whether to convert the size to an integer. Default is False.
		to_str (bool, optional): Whether to convert the size to a string representation. Default is False.
		str_format (str, optional): The format string to use when converting the size to a string. Default is '.2f'.

	Returns:
		int or str: The formatted size based on the provided arguments.

	Examples:
		>>> format_bytes(1500, use_1024_bytes=False)
		'1.50 K'
		>>> format_bytes('1.5 GiB', to_int=True)
		1610612736
		>>> format_bytes('1.5 GiB', to_str=True)
		'1.50 Gi'
		>>> format_bytes(1610612736, use_1024_bytes=True, to_str=True)
		'1.50 Gi'
		>>> format_bytes(1610612736, use_1024_bytes=False, to_str=True)
		'1.61 G'
	"""
	if to_int or isinstance(size, str):
		if isinstance(size, int):
			return size
		elif isinstance(size, str):
			# Use regular expression to split the numeric part from the unit, handling optional whitespace
			match = re.match(r"(\d+(\.\d+)?)\s*([a-zA-Z]*)", size)
			if not match:
				if to_str:
					return size
				print("Invalid size format. Expected format: 'number [unit]', e.g., '1.5 GiB' or '1.5GiB'")
				print(f"Got: {size}")
				return 0
			number, _, unit = match.groups()
			number = float(number)
			unit  = unit.strip().lower().rstrip('b')
			# Define the unit conversion dictionary
			if unit.endswith('i'):
				# this means we treat the unit as 1024 bytes if it ends with 'i'
				use_1024_bytes = True
			elif use_1024_bytes is None:
				use_1024_bytes = False
			unit  = unit.rstrip('i')
			if use_1024_bytes:
				power = 2**10
			else:
				power = 10**3
			unit_labels = {'': 0, 'k': 1, 'm': 2, 'g': 3, 't': 4, 'p': 5}
			if unit not in unit_labels:
				if to_str:
					return size
				print(f"Invalid unit '{unit}'. Expected one of {list(unit_labels.keys())}")
				return 0
			if to_str:
				return format_bytes(size=int(number * (power ** unit_labels[unit])), use_1024_bytes=use_1024_bytes, to_str=True, str_format=str_format)
			# Calculate the bytes
			return int(number * (power ** unit_labels[unit]))
		else:
			try:
				return int(size)
			except Exception as e:
				return 0
	elif to_str or isinstance(size, int) or isinstance(size, float):
		if isinstance(size, str):
			try:
				size = size.rstrip('B').rstrip('b')
				size = float(size.lower().strip())
			except Exception as e:
				return size
		# size is in bytes
		if use_1024_bytes or use_1024_bytes is None:
			power = 2**10
			n = 0
			power_labels = {0 : '', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti', 5: 'Pi'}
			while size > power:
				size /= power
				n += 1
			return f"{size:{str_format}}{' '}{power_labels[n]}"
		else:
			power = 10**3
			n = 0
			power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T', 5: 'P'}
			while size > power:
				size /= power
				n += 1
			return f"{size:{str_format}}{' '}{power_labels[n]}"
	else:
		try:
			return format_bytes(float(size), use_1024_bytes)
		except Exception as e:
			import traceback
			print(f"Error: {e}")
			print(traceback.format_exc())
			print(f"Invalid size: {size}")
		return 0

def get_resource_usage(return_dict = False):
    try:
        if RESOURCE_LIB_AVAILABLE:
            rawResource =  resource.getrusage(resource.RUSAGE_SELF)
            resourceDict = {}
            resourceDict['user mode time'] = f'{rawResource.ru_utime} seconds'
            resourceDict['system mode time'] = f'{rawResource.ru_stime} seconds'
            resourceDict['max resident set size'] = f'{format_bytes(rawResource.ru_maxrss * 1024)}B'
            resourceDict['shared memory size'] = f'{format_bytes(rawResource.ru_ixrss * 1024)}B'
            resourceDict['unshared memory size'] = f'{format_bytes(rawResource.ru_idrss * 1024)}B'
            resourceDict['unshared stack size'] = f'{format_bytes(rawResource.ru_isrss * 1024)}B'
            resourceDict['cached page hits'] = f'{rawResource.ru_minflt}'
            resourceDict['missed page hits'] = f'{rawResource.ru_majflt}'
            resourceDict['swapped out page count'] = f'{rawResource.ru_nswap}'
            resourceDict['block input operations'] = f'{rawResource.ru_inblock}'
            resourceDict['block output operations'] = f'{rawResource.ru_oublock}'
            resourceDict['IPC messages sent'] = f'{rawResource.ru_msgsnd}'
            resourceDict['IPC messages received'] = f'{rawResource.ru_msgrcv}'
            resourceDict['signals received'] = f'{rawResource.ru_nsignals}'
            resourceDict['voluntary context sw'] = f'{rawResource.ru_nvcsw}'
            resourceDict['involuntary context sw'] = f'{rawResource.ru_nivcsw}'
            if return_dict:
                return resourceDict
            return '\n'.join(['\t'.join(line) for line in resourceDict.items()])
    except Exception as e:
        print(f"Error: {e}")
        if return_dict:
            return {}
        return ''


def benchmark(tl):
	startTime = time.monotonic_ns()
	tl.teeprint("Starting benchmarkPerformance.py")
	for i in range(1000000):
		tl.info(f"This is a test message: {i}")
	endTime = time.monotonic_ns()
	tl.teeprint("Finished benchmarkPerformance.py")
	elapsedTime = endTime - startTime
	tl.teeok(f"Elapsed time: {elapsedTime / 1_000_000_000:.2f} seconds")

tl = Tee_Logger.teeLogger(programName="no_compression_t",binary_mode=False)
benchmark(tl)
tl.teeprint("Finished text mode benchmarkPerformance.py with no compression")

tl = Tee_Logger_Bin.teeLogger(programName="no_compression_b",binary_mode=True)
benchmark(tl)
tl.teeprint("Finished binary mode benchmarkPerformance.py with no compression")

tl = Tee_Logger.teeLogger(in_place_compression='xz',programName="xz_t",binary_mode=False)
benchmark(tl)
tl.teeprint("Finished text mode benchmarkPerformance.py with xz compression")

tl = Tee_Logger_Bin.teeLogger(in_place_compression='xz',programName="xz_b",binary_mode=True)
benchmark(tl)
tl.teeprint("Finished binary mode benchmarkPerformance.py with xz compression")



