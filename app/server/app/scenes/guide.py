"""Private spherical composition guide; never a delivered gameplay image.

The image model receives perspective, ground contact and open portals in the
same projection it must paint. A flat colour sheet gives it no spatial scale.
"""
from math import sin, cos, pi
from PIL import Image


def guide_half_extent(descriptor):
    # A local correction needs a legible threshold, not three subpixel treads
    # six metres away. This is private geometry; finished pixels are remeasured.
    if descriptor.get('routeRepairGuideVersion',0)<3:return 6
    pose=descriptor.get('viewpoint',{})
    return max(4,abs(pose.get('x',0))+1.5,abs(pose.get('z',0))+1.5)


def _intersects_walkway(box, portal, camera_x, camera_z):
    """Test a solid against the whole approach, including oblique neighbours."""
    x1, _, z1, x2, _, z2, _ = box
    _, px, pz, _, opening = portal
    dx, dz = px-camera_x, pz-camera_z
    distance = (dx*dx+dz*dz)**.5
    if distance < 1e-8:
        return True
    nx, nz = -dz/distance*(opening+.04), dx/distance*(opening+.04)
    corridor = [(camera_x+nx,camera_z+nz),(px+nx,pz+nz),
                (px-nx,pz-nz),(camera_x-nx,camera_z-nz)]
    rectangle = [(x1,z1),(x1,z2),(x2,z2),(x2,z1)]
    for ax, az in ((1,0),(0,1),(dx,dz),(-dz,dx)):
        a = [x*ax+z*az for x,z in corridor]
        b = [x*ax+z*az for x,z in rectangle]
        if max(a) <= min(b) or max(b) <= min(a):
            return False
    return True


def _portal_landmarks(grouped, camera_x, camera_z):
    boxes = []
    portals = [portal for group in grouped.values() for portal in group]
    for group in grouped.values():
        for portal in group:
            axis,px,pz,motif,opening = portal
            if motif not in ('market','residential'):continue
            wall=px if axis=='x' else pz;sign=1 if wall>0 else -1
            def portal_box(across1,across2,inset1,inset2,low,high,colour):
                a=wall-sign*inset1;b=wall-sign*inset2
                if axis=='x':return (min(a,b),low,pz+across1,max(a,b),high,pz+across2,colour)
                return (px+across1,low,min(a,b),px+across2,high,max(a,b),colour)
            if motif=='residential':
                for step in range(3):
                    # Distinct treads, rather than one flat grey ramp. The
                    # actual returned painting still requires independent proof.
                    box=portal_box(-opening*.85,opening*.85,.15+step*.35,.5+step*.35,0,(3-step)*.18,
                                   (155+step*12,149+step*12,136+step*12))
                    if not any(_intersects_walkway(box,p,camera_x,camera_z) for p in portals if p != portal):
                        boxes.append(box)
            else:
                boxes.append(portal_box(-opening-.55,opening+.55,.15,1.35,2.75,2.85,(188,151,91)))
                for side in (-1,1):
                    centre=side*(opening+.5)
                    furniture=[portal_box(centre-.32,centre+.32,.25,1.25,.7,.85,(124,83,47))]
                    furniture += [portal_box(centre-.035,centre+.035,inset,inset+.07,0,2.75,(105,79,56)) for inset in (.3,1.2)]
                    # Decorations must fit around all routes. Never narrow a
                    # real approach merely to make room for a market stall.
                    if not any(_intersects_walkway(b,p,camera_x,camera_z) for b in furniture for p in portals):
                        boxes.extend(furniture)
    return boxes


def portal_geometry(descriptor):
    """Shared private layout for painting guides and their local repair masks."""
    camera=descriptor.get('viewpoint',{})
    camera_x,camera_z=camera.get('x',0),camera.get('z',0)
    extent=guide_half_extent(descriptor)
    built_outdoor=descriptor.get('spaceKind') in ('open-air street or market','open-air courtyard')
    connections=descriptor.get('exitRoutes',[])
    routes=[e.get('yaw',0) for e in connections]
    portals=[]
    for connection,yaw in zip(connections,routes):
        tx,tz=sin(yaw),-cos(yaw)
        hit_x=((extent-camera_x)/tx if tx>1e-8 else (-extent-camera_x)/tx if tx< -1e-8 else float('inf'))
        hit_z=((extent-camera_z)/tz if tz>1e-8 else (-extent-camera_z)/tz if tz< -1e-8 else float('inf'))
        distance=min(hit_x,hit_z)
        cue=connection.get('visualCue','').lower()
        motif='tavern' if 'tavern' in cue else 'market' if 'market' in cue else 'residential' if 'townhouse' in cue else 'path'
        portals.append(('x' if hit_x<hit_z else 'z',camera_x+tx*distance,camera_z+tz*distance,motif,connection.get('id')))
    result=[]
    for axis,px,pz,motif,ident in portals:
        key=(axis,round(px if axis=='x' else pz))
        neighbours=[abs((pz-qz) if axis=='x' else (px-qx)) for qa,qx,qz,_,_ in portals
                    if qa==axis and round(qx if axis=='x' else qz)==key[1] and abs((pz-qz) if axis=='x' else (px-qx))>1e-6]
        opening=min(1.6 if built_outdoor else .85,min(neighbours,default=100)*.42)
        result.append({'id':ident,'axis':axis,'x':px,'z':pz,'motif':motif,'opening':opening})
    return result


def scene_guide(descriptor):
    width,height=1024,512
    camera=descriptor.get('viewpoint',{})
    camera_x,camera_z=camera.get('x',0),camera.get('z',0)
    extent=guide_half_extent(descriptor)
    interior=descriptor.get('spaceKind')=='interior'
    built_outdoor=descriptor.get('spaceKind') in ('open-air street or market','open-air courtyard')
    image=Image.new('RGB',(width,height));pixels=image.load()
    routes=[e.get('yaw',0) for e in descriptor.get('exitRoutes',[])]
    grouped={}
    for portal in portal_geometry(descriptor):
        axis,px,pz=portal['axis'],portal['x'],portal['z']
        grouped.setdefault((axis,round(px if axis=='x' else pz)),[]).append((axis,px,pz,portal['motif'],portal['opening']))
    boxes=_portal_landmarks(grouped,camera_x,camera_z)
    from .layout import route_fixture_links
    route_links=route_fixture_links(descriptor.get('exitRoutes',[]),descriptor.get('renderLayout',{}))
    stair_portals={ident for route in descriptor.get('exitRoutes',[])
                   if 'townhouse' in route.get('visualCue','').lower()
                   for ident in [route.get('portalId'),*route.get('associatedFixtureIds',[]),*route_links.get(route.get('id'),[])] if ident}
    # Give the painter physical landmark geometry, rather than a coloured
    # rectangle on a wall. These remain private generation input: all released
    # anchors must still be measured on the returned artwork.
    for node in descriptor.get('renderLayout',{}).get('nodes',[]):
        kind=node.get('fixture');cx,cz=node.get('worldX',0),node.get('worldZ',0)
        if kind in ('table','counter','bench'):
            bw,bh,bd=(1.3,.75,.9) if kind=='table' else (2.4,1.0,.8) if kind=='counter' else (1.8,.5,.55)
            boxes.append((cx-bw/2,bh-.12,cz-bd/2,cx+bw/2,bh,cz+bd/2,(124,91,61)))
            for lx in (-bw*.4,bw*.4):
                for lz in (-bd*.35,bd*.35):boxes.append((cx+lx-.05,0,cz+lz-.05,cx+lx+.05,bh,cz+lz+.05,(105,79,56)))
        elif kind=='steps':
            # A route-linked stair belongs to that doorway. The generic
            # waypoint coordinates must not create a second orphaned stair
            # elsewhere facing a solid wall.
            if node.get('id') in stair_portals:continue
            for step in range(3):
                boxes.append((cx-.7,0,cz-.5+step*.28,cx+.7,(step+1)*.17,cz+.5,(155,149,136)))
    longitudes=[(sin((x/width-.5)*2*pi),-cos((x/width-.5)*2*pi)) for x in range(width)]
    latitudes=[(sin((.5-y/height)*pi),cos((.5-y/height)*pi)) for y in range(height)]
    for y,(dy,cp) in enumerate(latitudes):
        for x,(sx,sz) in enumerate(longitudes):
            dx,dz=sx*cp,sz*cp
            wall_x=((extent-camera_x)/dx if dx>1e-8 else (-extent-camera_x)/dx if dx< -1e-8 else float('inf'))
            wall_z=((extent-camera_z)/dz if dz>1e-8 else (-extent-camera_z)/dz if dz< -1e-8 else float('inf'))
            horizontal=min(wall_x,wall_z)
            vertical=(1.6/-dy if dy<0 else 2.4/max(dy,1e-8) if interior else float('inf'))
            outdoor_wall=horizontal<vertical and 0<=1.6+dy*horizontal<6
            if (interior and horizontal<vertical) or (built_outdoor and outdoor_wall):
                distance=horizontal;wx,wz=camera_x+dx*distance,camera_z+dz*distance;wy=1.6+dy*distance
                x_wall=wall_x<wall_z
                colour=(182,176,162) if x_wall else (199,191,173)
                if wy<.3:colour=(122,101,81)
                nearby=grouped.get(('x' if x_wall else 'z',round(wx if x_wall else wz)),[])
                closest=min(nearby,key=lambda p:abs(wz-p[2]) if x_wall else abs(wx-p[1]),default=None)
                for axis,portal_x,portal_z,motif,opening in ([closest] if closest else []):
                    # An open, full height doorway, with continuous floor.
                    across=abs(wz-portal_z) if x_wall else abs(wx-portal_x)
                    samewall=(axis=='x' and abs(wx-portal_x)<.01 if x_wall else axis=='z' and abs(wz-portal_z)<.01)
                    if samewall and built_outdoor:
                        # Put the route's architecture around the SAME open
                        # threshold in the private geometry, rather than asking
                        # the painter to invent unrelated landmarks elsewhere.
                        if motif=='tavern':
                            if opening+.25<across<opening+1.4 and 1.1<wy<2.8:colour=(226,166,72)
                            if opening+.15<across<opening+.75 and 0<wy<1.05:colour=(105,71,42)
                            if across<opening+1.5 and 3.8<wy<4.15:colour=(102,66,40)
                        elif motif=='market':
                            if across<opening+1.4 and 2.7<wy<3.5:colour=(188,151,91)
                            if opening+.3<across<opening+1.4 and 0<wy<1.15:colour=(124,83,47)
                        elif motif=='residential' and opening+.1<across<opening+.35 and wy<1.15:colour=(105,79,56)
                    if samewall and across<opening and wy<(4.2 if built_outdoor else 2.7):
                        if built_outdoor and motif in ('residential','tavern'):
                            # The receiving room has a real ceiling and floor,
                            # not the sky/façade of another street. Residential
                            # floor height agrees with the top exterior tread.
                            # This remains private input, never route evidence.
                            floor_level=.54 if motif=='residential' else 0
                            floor=(1.6-floor_level)/-dy if dy<0 else float('inf')
                            ceiling=(3.6-1.6)/dy if dy>0 else float('inf')
                            depth=horizontal+7/max(cp,.01)
                            if horizontal<=floor<depth:
                                fx,fz=camera_x+dx*floor,camera_z+dz*floor
                                grain=abs((fx if axis=='x' else fz)*2%1)<.045
                                colour=((128,90,54) if grain else (176,133,84)) if motif=='tavern' else ((166,157,135) if grain else (207,198,174))
                            elif horizontal<=ceiling<depth:colour=(224,213,188)
                            else:
                                fy=1.6+dy*depth
                                colour=(169,132,92) if motif=='tavern' else (213,204,181)
                                if 1.2<fy<2.7 and across>opening*.4:colour=(224,176,101)
                        elif built_outdoor:
                            # A blue rectangle was repeatedly painted as a
                            # closed door. Show real receding walking ground and
                            # distant facade/sky THROUGH the opening instead.
                            floor=1.6/-dy if dy<0 else float('inf')
                            depth=horizontal+10/max(cp,.01)
                            fy=1.6+dy*depth
                            if floor<depth:
                                fx,fz=camera_x+dx*floor,camera_z+dz*floor
                                colour=(175,167,146) if min(fx%1,fz%1)<.04 else (201,187,156)
                            elif fy>6:colour=(181,205,219)
                            else:
                                colour=(171,169,151)
                                # Low receding material seams give perspective
                                # without creating a door leaf across the route.
                                if 1.2<fy<3.1 and across>.7:colour=(125,122,106)
                        else:colour=(119,150,155) if wy>.08 else (171,157,130)
            elif dy<0:
                distance=vertical;wx,wz=camera_x+dx*distance,camera_z+dz*distance
                grid=min(wx%1,wz%1)<.035
                colour=(145,136,117) if grid else (186,174,147)
                if not interior:
                    for yaw in routes:
                        along=(wx-camera_x)*sin(yaw)-(wz-camera_z)*cos(yaw)
                        across=abs((wx-camera_x)*cos(yaw)+(wz-camera_z)*sin(yaw))
                        if along>1 and across<.9:colour=(204,189,154)
            else:
                colour=(221,215,202) if interior else (181,205,219)
                distance=vertical if interior else float('inf')
            # Table/counter/bench blockouts provide scale and occlusion cues.
            # They are repainted by the image provider, never used as pixels in
            # the gameplay viewer or as proof of a visual anchor.
            for x1,y1,z1,x2,y2,z2,paint in boxes:
                near,far=0,float('inf')
                for low,high,start,direction in ((x1,x2,camera_x,dx),(y1,y2,1.6,dy),(z1,z2,camera_z,dz)):
                    if abs(direction)<1e-8:
                        if not low<=start<=high:far=-1;break
                    else:
                        a,b=(low-start)/direction,(high-start)/direction
                        near=max(near,min(a,b));far=min(far,max(a,b))
                if 0<near<distance and near<=far:distance=near;colour=paint
            pixels[x,y]=colour
    return image.resize((3136,1568),Image.Resampling.LANCZOS)
