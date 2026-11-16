import socket

import pytest

from connect_labs.hqdata.netguard import HostRefused, check_host, check_port


def resolver(*addresses):
    def resolve(host, port, type=None):
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses
        ]

    return resolve


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.8.8.8",
        "::1",
        "169.254.169.254",  # the metadata service
        "169.254.0.5",
        "fe80::1",
        "fd00:ec2::254",  # AWS's IPv6 metadata address
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::ffff:127.0.0.1",  # IPv4 loopback dressed as IPv6
        "::ffff:169.254.169.254",
        "64:ff9b::7f00:1",  # NAT64 carrying 127.0.0.1
        "2002:7f00:1::",  # 6to4 carrying 127.0.0.1
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "100.64.0.1",  # carrier-grade NAT
        "fc00::1",
    ],
)
def test_addresses_labs_must_not_be_steered_at_are_refused(address):
    with pytest.raises(HostRefused):
        check_host("db.example.org", resolver=resolver(address))


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "::1", "169.254.169.254", "fe80::1", "0.0.0.0", "224.0.0.1", "::ffff:127.0.0.1"]
)
def test_even_staff_cannot_allow_the_ones_that_are_never_a_customers(address):
    with pytest.raises(HostRefused):
        check_host("db.example.org", allow_private=True, resolver=resolver(address))


@pytest.mark.parametrize("address", ["10.1.2.3", "172.16.0.9", "192.168.1.1", "100.64.0.1", "fc00::1"])
def test_a_customer_on_a_private_network_is_allowed_only_when_staff_say_so(address):
    with pytest.raises(HostRefused):
        check_host("db.example.org", resolver=resolver(address))
    assert check_host("db.example.org", allow_private=True, resolver=resolver(address)) == address


def test_a_public_address_is_returned_to_connect_to():
    assert check_host("db.example.org", resolver=resolver("93.184.216.34")) == "93.184.216.34"


def test_one_bad_address_among_good_ones_refuses_the_name():
    """A name that answers with a public and an internal address is how rebinding gets a second try."""
    with pytest.raises(HostRefused):
        check_host("db.example.org", resolver=resolver("93.184.216.34", "10.0.0.5"))
    with pytest.raises(HostRefused):
        check_host("db.example.org", resolver=resolver("10.0.0.5", "93.184.216.34"))


def test_ipv4_is_preferred_when_a_name_has_both():
    assert check_host("db.example.org", resolver=resolver("2606:4700::1111", "93.184.216.34")) == "93.184.216.34"


def test_an_ip_literal_is_checked_without_asking_dns():
    def never(*a, **k):
        raise AssertionError("DNS was asked about a literal")

    assert check_host("93.184.216.34", resolver=never) == "93.184.216.34"
    with pytest.raises(HostRefused):
        check_host("127.0.0.1", resolver=never)
    with pytest.raises(HostRefused):
        check_host("169.254.169.254", resolver=never)


@pytest.mark.parametrize(
    "host",
    [
        "",
        "   ",
        "db example.org",
        "db.example.org/x",
        "user@db.example.org",
        "db.example.org:5432",
        "-bad.example.org",
        "a" * 300,
        "db_x.example.org",
    ],
)
def test_a_string_that_is_not_a_host_name_is_refused_before_dns(host):
    def never(*a, **k):
        raise AssertionError("DNS was asked about a malformed host")

    with pytest.raises(HostRefused):
        check_host(host, resolver=never)


def test_a_name_that_does_not_resolve_is_refused_with_a_reason():
    def nope(*a, **k):
        raise socket.gaierror("no such host")

    with pytest.raises(HostRefused, match="does not resolve"):
        check_host("nope.example.org", resolver=nope)
    with pytest.raises(HostRefused, match="does not resolve"):
        check_host("nope.example.org", resolver=lambda *a, **k: [])


def test_labs_own_database_is_never_a_customers(settings):
    settings.DATABASES = {"default": {"HOST": "labs-db.internal.example"}}
    with pytest.raises(HostRefused, match="labs database"):
        check_host("labs-db.internal.example", allow_private=True, resolver=resolver("10.0.0.5"))


def test_a_trailing_dot_and_capitals_do_not_get_past_it(settings):
    settings.DATABASES = {"default": {"HOST": "labs-db.internal.example"}}
    with pytest.raises(HostRefused):
        check_host("LABS-DB.internal.example.", allow_private=True, resolver=resolver("10.0.0.5"))


@pytest.mark.parametrize(
    "port,ok",
    [(5432, True), ("5432", True), (1, True), (65535, True), (0, False), (65536, False), ("x", False), (None, False)],
)
def test_ports(port, ok):
    if ok:
        assert check_port(port) == int(port)
    else:
        with pytest.raises(HostRefused):
            check_port(port)
