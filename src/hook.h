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

#define MAX_FIRST_RADAR_SCANS	3  /* How many time we have to retry 
				      the first radar_scan if we
				      didn't found anything */

/* Used for: ip.data[0]=HOOKING_IP; */
#define HOOKING_IP  0x100000a      /* 10.0.0.1  (in network order) */
#define HOOKING_IP6 0xc0fe	   /* fec0:: */

/* 
 * The free_nodes pkt is used to send the list of all the free/not used
 * nodes/gnodes present at level fn_hdr.level. 
 * If level is == 0 then the pkt contains the list of free nodes of the fn_hdr.gid
 * gnode. 
 * If level is > 0 then it contains the list of free gnodes which are inside
 * the gnode with `fn_hdr.gid' gid of the `fn_hdr.level'th level. So the free gnodes
 * are part of the (fn_hdr.level - 1)th level.
 */
struct free_nodes_hdr
{
	u_char 		max_levels;	/* How many levels we are managing */

	int	 	ipstart[MAX_IP_INT];	/* The ipstart of the gnode */
	u_char 		level;		/* The level where the gnode belongs */
	u_char  	gid;		/* The gnode id */
	u_char		nodes;		/* The number of free nodes/gnodes - 1 */
}_PACKED_;
#define FREE_NODES_SZ(nodes) (sizeof(struct free_nodes_hdr)  	      +\
					    (sizeof(u_char) * (nodes)))

/* 
 * the free_nodes block is:
 *
 *	u_char		free_nodes[fn_hdr.nodes/8]; The bit x inside `free_nodes'
 *						    tells if the node x, i.e.
 *						    int_map[x], is up or not.
 *						    If the bit is set to 1, it
 *						    is.
 *
 * The free_nodes pkt is:
 *	fn_hdr;
 *	fn_block;
 */

/* 
 * the qspn_round pkt it:
 * 	u_char 		max_levels;
 *	int		qspn_id[max_levels];	   the qspn_id of the last qspn_round for each 
 *						   fn_hdr.max_levels level
 *	struct timeval  qtime[max_levels];         qspn round time: how many seconds passed away
 *						   since the previous qspn round. There's a qtime
 *						   for each fn_hdr.max_levels level
 */
/* Note: for this int_info we are considering the timeval array as one int
 * with `max_levels'*2 members */
INT_INFO qspn_round_pkt_iinfo = { 2, 
				  { INT_TYPE_32BIT, INT_TYPE_32BIT }, 
				  { sizeof(char), IINFO_DYNAMIC_VALUE },
				  { IINFO_DYNAMIC_VALUE, IINFO_DYNAMIC_VALUE }
				};
	
#define QSPN_ROUND_PKT_SZ(levels)	(sizeof(u_char) + 			\
						((levels) * sizeof(int)) +	\
			                          ((levels) * sizeof(struct timeval)) )


/* * * Functions declaration * * */
	
int put_free_nodes(PACKET rq_pkt);
int put_qspn_round(PACKET rq_pkt);
int put_ext_map(PACKET rq_pkt);
int put_int_map(PACKET rq_pkt);
int put_bnode_map(PACKET rq_pkt);

int create_gnodes(inet_prefix *ip, int final_level);
void set_ip_and_def_gw(char *dev, inet_prefix ip);

int hook_init(void);
int netsukuku_hook(void);