import re
import logging


# Singleton implementation for Addrcheck
class Addrcheck(object):
    """
    Singleton implementation for Addrcheck. Note it is important not
    to initialise "self._method" by creating a "__init__" function
    since this would be called whenever asking for the singleton...
    (Addrcheck() would call __init__).
    """
    __instance = None

    def __new__(cls):
        """
        Returns Singleton of Addrcheck (create if not yet existing)

        Returns:
            (Addrcheck) The singleton of Addrcheck
        """
        if Addrcheck.__instance is None:
            Addrcheck.__instance = object.__new__(cls)
            Addrcheck.__instance.set("Default")
        return Addrcheck.__instance

    def set(self, name):
        """
        Sets method to be used in valid - function to validate an address
        Args:
            name (String): String with name of validator
        """
        logger = logging.getLogger("%s.Addrcheck"%__package__)
        if name == "Default":
            logger.info("Set default address checker method")
            self._method = Default()
        elif name == "LazyLocalPart":
            logger.info("Set LazyLocalPart address checker method")
            self._method = LazyLocalPart()
        else:
            logger.warning("Mail address check \"%s\" not valid, using default..."%name)
            self._method = Default()

    def valid(self, address):
        """

        Args:
            address (String): Address to be checked

        Returns:
            (Boolean) True if address is valid using internal validation method

        """
        return self._method(address)


class Addrcheckint(object):
    """
    Functor interface for method called by Addrcheck
    """
    def __init__(self):
        pass
    def __call__(self, mailAddress):
        raise NotImplemented

class Default(Addrcheckint):
    """
    Default implementation (and backward compatible) which does not allow more than one '@'
    """
    def __init__(self):
        super(Default, self).__init__()
    def __call__(self,mailAddress):
        leg =  (mailAddress !='' and  (   re.match(r"[^@]+@[^@]+$", mailAddress)))
        if not leg:
            logger = logging.getLogger("postomaat.Addrcheck.Default")
            logger.warning("Address validation check failed for: %s"%mailAddress)
        return leg

class LazyLocalPart(Addrcheckint):
    """
    Allows '@' in local part.

    Note:
    In fuglu the original envelope address is received. For the policy daemon protocol the
    address is not quoted anymore. If there are quotes around the local part it is removed
    by Postfix before passed to Postomaat.
    Knowing:
    - there is only one mail address in the string
    - address has been RFC compliant because it was received by Postomaat
    So to make LazyLocalPart consistent with fuglu's version there's no need to check
    distinguis a quoted and an unquoted localpart.
    """
    def __init__(self):
        super(LazyLocalPart, self).__init__()
    def __call__(self,mailAddress):
        leg = ( mailAddress !='' and  ( re.match(r"^[\x00-\x7f]+@[^@]+$", mailAddress) ))
        # here, the address received does not contain quotes. quoted local parts can
        # contain more characters than unquoted according.
        if not leg:
            logger = logging.getLogger("postomaat.Addrcheck.LazyLocalPart")
            logger.warning("Address validation check failed for: %s"%mailAddress)
        return leg

