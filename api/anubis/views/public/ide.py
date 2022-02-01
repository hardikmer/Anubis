import copy
from datetime import datetime, timedelta
from typing import Dict

from flask import Blueprint, request

from anubis.lms.courses import is_course_admin
from anubis.lms.theia import (
    get_n_available_sessions,
    theia_poll_ide,
    theia_redirect_url,
    initialize_ide,
    assert_theia_sessions_enabled,
)
from anubis.models import Assignment, AssignmentRepo, TheiaSession, db, THEIA_DEFAULT_OPTIONS
from anubis.utils.auth.http import require_user
from anubis.utils.auth.user import current_user
from anubis.utils.data import req_assert
from anubis.utils.http import error_response, success_response
from anubis.utils.http.decorators import json_response, load_from_id
from anubis.utils.rpc import enqueue_ide_stop
from anubis.utils.config import get_config_int
from anubis.utils.cache import cache

ide_ = Blueprint("public-ide", __name__, url_prefix="/public/ide")


@ide_.post("/initialize/<string:id>")
@require_user()
@load_from_id(Assignment, verify_owner=False)
@json_response
def public_ide_initialize(assignment: Assignment):
    """
    Redirect to theia proxy.

    :param assignment:
    :return:
    """

    # verify that ides are enabled for this assignment
    req_assert(assignment.ide_enabled, message="IDEs are not enabled for this assignment")

    # Check for existing active session
    active_session = (
        TheiaSession.query.join(Assignment)
        .filter(
            TheiaSession.owner_id == current_user.id,
            TheiaSession.assignment_id == assignment.id,
            TheiaSession.active,
        )
        .first()
    )

    # If there was an existing session for this assignment found, skip
    # the initialization, and return the active session information.
    if active_session is not None:
        return success_response({"active": active_session.active, "session": active_session.data})

    # Check last session
    last_session: TheiaSession = TheiaSession.query.filter(
        TheiaSession.owner_id == current_user.id,
        TheiaSession.active == False,
    ).order_by(TheiaSession.created.desc()).limit(1).first()

    # Check if last session had a persistent volume
    if last_session and last_session.persistent_storage:
        # If it did, then we need to make sure the volume
        # has had time to unmount.
        seconds_passed = (datetime.now() - last_session.ended).total_seconds()
        cooldown_seconds = get_config_int('THEIA_VOLUME_COOLDOWN_SECONDS', 1)

        # If within cooldown time, then give back a warning
        if seconds_passed < cooldown_seconds:
            return success_response({
                'status': 'Please wait a few more seconds. '
                          'Your last IDEs home volume is still unmounting.',
                'variant': 'warning',
            })

    # Assert that new ide starts are allowed. If they are not, then
    # we return a status message to the user saying they are not able
    # to start a new ide.
    assert_theia_sessions_enabled()

    # If the user requesting this IDE is a course admin (ta/professor/superuser), then there
    # are a few places we handle things differently.
    is_admin = is_course_admin(assignment.course_id)

    # If it is a student (not a ta) requesting the ide, then we will need to
    # make sure that the assignment has actually been released.
    if not is_admin:

        # If the assignment has been released, then we cannot allocate a session to a student
        req_assert(
            assignment.release_date < datetime.now(),
            message="Assignment has not been released",
        )

        # If 3 weeks has passed since the assignment has been due, then we should not allow
        # new sessions to be created
        if assignment.due_date + timedelta(days=3 * 7) <= datetime.now():
            return error_response("Assignment due date passed over 3 weeks ago. IDEs are disabled.")

    # If github repos are enabled for this assignment, then we will
    # need to get the repo url.
    repo_url: str = ""
    if assignment.github_repo_required:
        # Make sure github username is set
        req_assert(
            current_user.github_username is not None,
            message="Please link your github account github account on profile page.",
        )

        # Make sure we have a repo we can use
        repo: AssignmentRepo = AssignmentRepo.query.filter(
            AssignmentRepo.owner_id == current_user.id,
            AssignmentRepo.assignment_id == assignment.id,
        ).first()

        # Verify that the repo exists
        req_assert(
            repo is not None,
            message="Anubis can not find your assignment repo. "
            "Please make sure your github username is set and is correct.",
        )
        # Update the repo url
        repo_url = repo.repo_url

    # Create the theia options from the assignment default
    options = copy.deepcopy(assignment.theia_options)

    # Figure out options from user values
    autosave = request.args.get("autosave", "true") == "true"
    persistent_storage = request.args.get("persistent_storage", "true") == "true"

    # Figure out options from assignment
    network_policy = options.get("network_policy", "os-student")
    resources = options.get(
        "resources",
        THEIA_DEFAULT_OPTIONS['resources'],
    )

    # If course admin, then give admin network policy
    if is_admin:
        network_policy = 'admin'

    # Create the theia session with the proper settings
    session: TheiaSession = initialize_ide(
        image_id=assignment.theia_image_id,
        assignment_id=assignment.id,
        course_id=assignment.course_id,
        repo_url=repo_url,
        playground=False,
        network_locked=not is_admin,
        network_policy=network_policy,
        persistent_storage=persistent_storage,
        autosave=autosave,
        resources=resources,
        privileged=False,
        admin=is_admin,
        credentials=is_admin,
    )

    return success_response({
        "active": session.active,
        "session": session.data,
        "status": "Session created",
    })


@ide_.route("/available")
@require_user()
@json_response
def public_ide_available():
    """
    List all sessions, active and inactive

    :return:
    """

    # Get the active and maximum number of ides currently allocated
    active_count, max_count = get_n_available_sessions()

    # Calculate if sessions are available
    session_available: bool = active_count < max_count

    # pass back if sessions are available
    return success_response(
        {
            "session_available": session_available,
        }
    )


@ide_.route("/active/<string:assignment_id>")
@require_user()
@json_response
def public_ide_active(assignment_id):
    """
    List all sessions, active and inactive

    :return:
    """

    # Find if they have an active session for this assignment
    session = TheiaSession.query.filter(
        TheiaSession.active,
        TheiaSession.owner_id == current_user.id,
        TheiaSession.assignment_id == assignment_id,
    ).first()

    # If they do not have an active assignment, then pass back False
    if session is None:
        return success_response({"active": False})

    # If they do have a session, then pass back True
    return success_response(
        {
            "active": True,
            "session": session.data,
        }
    )


@ide_.route("/stop/<string:theia_session_id>")
@require_user()
def public_ide_stop(theia_session_id: str) -> Dict[str, str]:
    """
    Endpoint for users to request a stop of their IDE. We need to mark the
    IDE as stopped in the database, and enqueue a job to clean up the
    existing kubernetes resources.

    :param theia_session_id:
    :return:
    """

    # Find the theia session
    theia_session: TheiaSession = TheiaSession.query.filter(
        TheiaSession.id == theia_session_id,
        TheiaSession.owner_id == current_user.id,
    ).first()

    # Verify that the session exists
    req_assert(theia_session is not None, message="session does not exist")

    # Mark the session as stopped.
    theia_session.active = False
    theia_session.ended = datetime.now()
    theia_session.state = "Ended"

    # Commit the change
    db.session.commit()

    # Enqueue a ide stop job
    enqueue_ide_stop(theia_session.id)

    # Clear poll cache
    cache.delete_memoized(theia_poll_ide, theia_session_id, current_user.id)

    # Pass back the status
    return success_response(
        {
            "status": "Session stopped.",
            "variant": "warning",
        }
    )


@ide_.route("/poll/<string:theia_session_id>")
@require_user()
@json_response
def public_ide_poll(theia_session_id: str) -> Dict[str, str]:
    """
    Slightly cached endpoint for polling for session data.

    :param theia_session_id:
    :return:
    """

    # Find the (possibly cached) session data
    session_data = theia_poll_ide(theia_session_id, current_user.id)

    # Assert that the session exists
    req_assert(session_data is not None, message="session does not exist")

    # Check to see if it is still initializing
    session_state = session_data["state"]
    loading = session_state not in {"Running", "Ended", "Failed"}

    # Map of session state code to the status message that should
    # be displayed on the frontend.
    status, variant = {
        "Running": ("Session is now ready.", "success"),
        # "Ended": ("Session ended.", "warning"),
        "Failed": ("Session failed to start. Please try again.", "error"),
    }.get(session_state, (None, None))

    # Pass back the status and data
    return success_response(
        {
            "loading": loading,
            "session": session_data,
            "status": status,
            "variant": variant,
        }
    )


@ide_.route("/redirect-url/<string:theia_session_id>")
@require_user()
@json_response
def public_ide_redirect_url(theia_session_id: str) -> Dict[str, str]:
    """
    Get the redirect url for a given session

    :param theia_session_id:
    :return:
    """

    # Search for session
    theia_session: TheiaSession = TheiaSession.query.filter(
        TheiaSession.id == theia_session_id,
        TheiaSession.owner_id == current_user.id,
    ).first()

    # Verify that the session exists
    req_assert(theia_session is not None, message="session does not exist")

    # Pass back redirect link
    return success_response({"redirect": theia_redirect_url(theia_session.id, current_user.netid)})
