CC = gcc
CFLAGS = -O2 -std=c99 -Wall -Wextra
BUILDDIR = build

.PHONY: qufc_test clean

qufc_test: $(BUILDDIR)/qufc_test
	@$(BUILDDIR)/qufc_test

$(BUILDDIR):
	@mkdir -p $(BUILDDIR)

$(BUILDDIR)/qufc_test: $(BUILDDIR)/qufc.o $(BUILDDIR)/qufc_test.o | $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ $^

$(BUILDDIR)/qufc.o: qufc.c qufc.h | $(BUILDDIR)
	$(CC) $(CFLAGS) -c -o $@ qufc.c

$(BUILDDIR)/qufc_test.o: qufc_test.c qufc.h | $(BUILDDIR)
	$(CC) $(CFLAGS) -c -o $@ qufc_test.c

clean:
	rm -rf $(BUILDDIR)

# ---- the deck bridge: quilt-vm-c (vendored, read-only) + qufc + bridge ----
QV = $(HOME)/projects/quilt-esp32/firmware
VMSRC = $(QV)/vm/quilt_vm.c $(QV)/qm_opcodes.c

.PHONY: deckbridge all

all: qufc_test deckbridge

$(BUILDDIR)/deckbridge: deckbridge.c $(BUILDDIR)/qufc.o | $(BUILDDIR)
	$(CC) $(CFLAGS) -I$(QV) -I$(QV)/vm -I. -o $@ deckbridge.c $(BUILDDIR)/qufc.o $(VMSRC)

deckbridge: $(BUILDDIR)/deckbridge
	@echo "deckbridge built: $(BUILDDIR)/deckbridge"
