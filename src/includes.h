/* This file is part of Netsukuku
 * (c) Copyright 2004 Andrea Lo Pumo aka AlpT <alpt@freaknet.org>
 *
 * This source code is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License as published 
 * by the Free Software Foundation; either version 2 of the License,
 * or (at your option) any later version.
 *
 * This source code is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * Please refer to the GNU Public License for more details.
 *
 * You should have received a copy of the GNU Public License along with
 * this source code; if not, write to:
 * Free Software Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

#ifndef INCLUDES_H
#define INCLUDES_H

#include "config.h"

#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <syslog.h>
#include <stdarg.h>
#include <errno.h>
#include <sys/time.h>

#include <asm/bitops.h>

/*socket*/
#include <sys/types.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <sys/sendfile.h>
#include <netinet/in.h>
#include <netinet/in_systm.h>
#include <net/if.h>

#include <sys/stat.h>

#include <time.h>

#include <netdb.h>
#include <unistd.h>
#include <getopt.h>

#include <sys/ioctl.h>
#include <fcntl.h>

#include <limits.h>
#include <signal.h>

#include <gmp.h>
#include <pthread.h>


#define _PACKED_ __attribute__ ((__packed__))

#define DEBUG_TEST

/* Actually the IPV6 is disabled */
#define IPV6_DISABLED

#ifdef DEBUG
#warning the DEBUG code is being built!
#include <execinfo.h>
#define ANDNA_DEBUG
#endif

#endif /*INCLUDES_H*/
