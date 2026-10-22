from atsfill.adapters.ashby import Ashby
from atsfill.adapters.bamboohr import BambooHR
from atsfill.adapters.breezy import Breezy
from atsfill.adapters.dayforce import Dayforce
from atsfill.adapters.greenhouse import Greenhouse
from atsfill.adapters.hireology import Hireology
from atsfill.adapters.hiringthing import HiringThing
from atsfill.adapters.jazzhr import JazzHR
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
from atsfill.adapters.teamtailor import Teamtailor
from atsfill.adapters.workable import Workable
from atsfill.adapters.wttj import WelcomeToTheJungle
from atsfill.adapters.zohorecruit import ZohoRecruit
from atsfill.core.adapter import Adapter


def adapters() -> list[Adapter]:
    """One fresh instance of each adapter: a command's adapters never see another posting."""
    return [
        Ashby(),
        Teamtailor(),
        Greenhouse(),
        WelcomeToTheJungle(),
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
        Phenom(),  # last: it claims a URL by its path alone
    ]
