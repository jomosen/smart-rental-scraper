from .browser.navigator import IPageNavigator
from .browser.interactor import IPageInteractor
from .browser.reader import IPageReader


class IBrowserDriver(IPageNavigator, IPageInteractor, IPageReader):
    """
    Full browser driver contract: composes the three focused sub-interfaces.

    ISP: concrete scrapers that only need a subset of capabilities can
    type-hint against IPageNavigator, IPageInteractor or IPageReader
    directly, without depending on the entire IBrowserDriver surface.

    PlaywrightDriver (and any other implementation) continues to implement
    IBrowserDriver and automatically satisfies all three sub-interfaces.
    """
