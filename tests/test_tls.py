import ssl
import unittest
from grocery.providers import tls_context


class TLSConfigurationTests(unittest.TestCase):
    def test_https_verifies_certificates_and_hostnames_with_loaded_roots(self):
        context = tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(len(context.get_ca_certs()), 0)
