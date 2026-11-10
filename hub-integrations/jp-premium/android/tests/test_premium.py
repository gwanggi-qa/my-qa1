import pytest

pytestmark = pytest.mark.state_changing


@pytest.mark.tc("JPP-0001")
def test_greeting_balloon(check):
    check("JPP-0001")


@pytest.mark.tc("JPP-0002")
def test_morning_popup(check):
    check("JPP-0002")


@pytest.mark.tc("JPP-0003")
def test_teacher_practice(check):
    check("JPP-0003")


@pytest.mark.tc("JPP-0004")
def test_farewell_popup(check):
    check("JPP-0004")


@pytest.mark.tc("JPP-0005")
def test_conversation_first(check):
    check("JPP-0005")


@pytest.mark.tc("JPP-0006")
def test_conversation_second(check):
    check("JPP-0006")


@pytest.mark.tc("JPP-0007")
def test_first_meeting_popup(check):
    check("JPP-0007")


@pytest.mark.tc("JPP-0008")
def test_regards_balloon(check):
    check("JPP-0008")


@pytest.mark.tc("JPP-0009")
def test_regards_popup(check):
    check("JPP-0009")


@pytest.mark.tc("JPP-0010")
def test_goodnight_popup(check):
    check("JPP-0010")
