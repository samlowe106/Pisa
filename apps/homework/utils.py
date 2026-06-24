def display_name(user):
    """A user's full name, falling back to their username."""
    return user.get_full_name() or user.username
