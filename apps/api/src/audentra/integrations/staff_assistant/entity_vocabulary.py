"""Navigation, calendar and domain nouns shared by both entity resolution paths."""

import re

NON_NAME_PHRASES = re.compile(
    r"\b(?:Action Cent(?:er|re)|Morning Brew|Task Board|Work Queue|Student Accounts|Student Health"
    r"|Student Life|Financial Aid|Academic Advising|International Student Services"
    r"|New Student Programs|Enrollment (?:Services|Support|Management|Leadership)"
    r"|Residence Life|Registrar|Admissions|Housing|Health Services|Fall|Spring|Summer"
    r"|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
    r"|January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Edward|Audentra|Aster|Chemistry|Psychology|Biology|Engineering|Nursing|Business"
    r"|Computer Science|Civil Engineering|English Literature|Mathematics|Physics|Economics"
    r"|History|Sociology|Political Science|Environmental Science|Health Sciences|Arts"
    r"|Humanities|Education|Communication|Design|Music|Philosophy|Kinesiology"
    r"|Accounting|Finance|Marketing|Statistics|Architecture|Anthropology|Geography"
    r"|Transcript|Immunization|Deposit|Orientation|Verification|FAFSA|SEVIS|I-20)\b"
)
