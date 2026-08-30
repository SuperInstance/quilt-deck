#include "qufc.h"
#include <stdio.h>
#include <string.h>

int main(void) {
	fprintf(stderr, "Starting selftest...\n");
	if (qufc_selftest() == 0) {
		printf("qufc_test PASS: golden vector byte-exact + round-trip\n");
		return 0;
	} else {
		printf("qufc_test FAIL\n");
		return 1;
	}
}
