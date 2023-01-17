import argparse
import traceback
import sys
import os

from anubis_autograde.exercise.get import set_exercises
from anubis_autograde.logging import log


def init_exercises(args: argparse.Namespace):

    try:
        module_path = args.exercise_module[:-3] if args.exercise_module.endswith('.py') else args.exercise_module
        module_directory = os.path.dirname(module_path)
        module_name = os.path.basename(module_path)
        sys.path.append(module_directory)
        exercise_module = __import__(module_name)
    except Exception as e:
        log.error(traceback.format_exc())
        log.error(f'Failed to import exercise module e={e}')
        exit(1)

    try:
        exercises, start_message, end_message = set_exercises(
            exercise_module.exercises,
            exercise_module.start_message,
            exercise_module.end_message,
        )
    except Exception as e:
        log.error(traceback.format_exc())
        log.error(f'Failed to import exercise module e={e}')
        exit(1)

    log.info(f'loaded exercises exercises={exercises}')
    log.debug(f'start_message={start_message}')
    log.debug(f'end_message={end_message}')

    resume = args.resume
    if resume:
        log.info(f'resume = {resume}')

        contains_resume = any(exercise.name == resume for exercise in exercises)
        if not contains_resume:
            log.warning(f'loaded exercises does not contain resume={resume}. resetting to beginning.')
            return

        for exercise in exercises:
            exercise.complete = True
            if exercise.name == resume:
                break

