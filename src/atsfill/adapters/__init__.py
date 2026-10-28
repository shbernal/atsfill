from atsfill.adapters.ashby import Ashby
from atsfill.adapters.bamboohr import BambooHR
from atsfill.adapters.breezy import Breezy
from atsfill.adapters.dayforce import Dayforce
from atsfill.adapters.eightfold import Eightfold
from atsfill.adapters.greenhouse import Greenhouse
from atsfill.adapters.gupy import Gupy
from atsfill.adapters.hireology import Hireology
from atsfill.adapters.hiringthing import HiringThing
from atsfill.adapters.isolvedhire import IsolvedHire
from atsfill.adapters.jazzhr import JazzHR
from atsfill.adapters.jobaffinity import JobAffinity
from atsfill.adapters.join import Join
from atsfill.adapters.lever import Lever
from atsfill.adapters.manatal import Manatal
from atsfill.adapters.oracle import Oracle
from atsfill.adapters.paycor import Paycor
from atsfill.adapters.paylocity import Paylocity
from atsfill.adapters.personio import Personio
from atsfill.adapters.phenom import Phenom
from atsfill.adapters.pinpoint import Pinpoint
from atsfill.adapters.recruitee import Recruitee
from atsfill.adapters.rippling import Rippling
from atsfill.adapters.smartrecruiters import SmartRecruiters
from atsfill.adapters.successfactors import SuccessFactors
from atsfill.adapters.teamtailor import Teamtailor
from atsfill.adapters.ukg import UKG
from atsfill.adapters.workable import Workable
from atsfill.adapters.workatastartup import WorkAtAStartup
from atsfill.adapters.workday import Workday
from atsfill.adapters.wttj import WelcomeToTheJungle
from atsfill.adapters.zohorecruit import ZohoRecruit
from atsfill.core.adapter import Adapter, Filtered


def adapters() -> list[Adapter]:
    """One fresh instance of each adapter: a command's adapters never see another posting."""
    return [
        Ashby(),
        Teamtailor(),
        Greenhouse(),
        WelcomeToTheJungle(),
        WorkAtAStartup(),
        Join(),
        JobAffinity(),
        SmartRecruiters(),
        Oracle(),
        Manatal(),
        Paylocity(),
        Workable(),
        Hireology(),
        Paycor(),
        JazzHR(),
        Lever(),
        Dayforce(),
        Recruitee(),
        Breezy(),
        BambooHR(),
        ZohoRecruit(),
        Personio(),
        Rippling(),
        Pinpoint(),
        HiringThing(),
        Eightfold(),
        Workday(),
        Gupy(),
        UKG(),
        IsolvedHire(),
        SuccessFactors(),  # it claims a career site's job path on any host
        Phenom(),  # last: it claims a URL by its path alone
    ]


def filters() -> list[str]:
    """The cosmetic filters of each adapter whose system's banner the blocker's lists miss."""
    return [line for a in adapters() if isinstance(a, Filtered) for line in a.filters]
