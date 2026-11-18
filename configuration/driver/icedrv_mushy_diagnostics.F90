!=======================================================================
! Optional standalone-driver diagnostics for enthalpy-based mushy validation.
! Column physics returns values through public interfaces and performs no I/O.
      module icedrv_mushy_diagnostics

      use icedrv_kinds
      use icedrv_constants, only: nu_diag
      use icedrv_domain_size, only: nx, ncat, nilyr, nslyr
      use icedrv_calendar, only: istep, dt
      use icedrv_state, only: aice, aicen, vicen, vsnon, trcrn, trcr, vice
      use icedrv_flux, only: fresh, fresh_ai, fsalt_ai, fhocn_ai, fsnow, frain, evap, &
          frazil, frazil_diag, sss, fpond, l_mpond_fresh, dpnd_flush, dpnd_expon, &
          dpnd_freebd, dpnd_initial, dpnd_dlid, dpnd_melt, dpnd_ridge, congel, meltb
      use icedrv_flux, only: sst, Tf
      use icepack_intfc, only: icepack_query_parameters, icepack_query_tracer_indices, &
          icepack_query_tracer_flags, icepack_mushy_state, icepack_snow_temperature, &
          icepack_warnings_flush, icepack_warnings_aborted
      use icedrv_system, only: icedrv_system_abort

      implicit none
      private
      public :: mushy_diagnostics, mushy_diagfreq, mushy_profilefreq, mushy_sample, mushy_begin, &
          mushy_before_coupling, mushy_end, mushy_write_energy

      logical (kind=log_kind) :: mushy_diagnostics = .false.
      integer (kind=int_kind) :: mushy_diagfreq = 1
      integer (kind=int_kind) :: mushy_profilefreq = 1
      integer :: column_unit, layer_unit, snow_unit, energy_unit
      logical :: opened = .false.
      real (kind=dbl_kind) :: initial(5,nx), raw_pond(nx), fresh_before(nx)

      contains

      logical function mushy_sample()
      mushy_sample = .false.
      if (.not. mushy_diagnostics) return
      if (mushy_diagfreq < 1) call icedrv_system_abort( &
          string='mushy_diagfreq must be positive',file=__FILE__,line=__LINE__)
      if (mushy_profilefreq < 1) call icedrv_system_abort( &
          string='mushy_profilefreq must be positive',file=__FILE__,line=__LINE__)
      mushy_sample = mod(istep,mushy_diagfreq) == 0
      end function mushy_sample

      subroutine check_io(status)
      integer, intent(in) :: status
      if (status /= 0) call icedrv_system_abort( &
          string='mushy diagnostic file I/O failed',file=__FILE__,line=__LINE__)
      end subroutine check_io

      subroutine check_physics()
      call icepack_warnings_flush(nu_diag)
      if (icepack_warnings_aborted()) call icedrv_system_abort( &
          string='mushy diagnostic public interface failed',file=__FILE__,line=__LINE__)
      end subroutine check_physics

      subroutine open_outputs()
      integer :: status
      if (opened) return
      open(newunit=column_unit,file='mushy_columns.csv',status='replace',action='write',iostat=status)
      call check_io(status)
      write(column_unit,'(a)',iostat=status) &
          'step,cell,l_mpond_fresh,dt_s,water_initial_kg_m2,water_final_kg_m2,'// &
          'salt_initial_kg_m2,salt_final_kg_m2,energy_initial_J_m2,energy_final_J_m2,'// &
          'fresh_ocean_kg_m2_s,salt_ocean_kg_m2_s,heat_ocean_W_m2,fsnow_kg_m2_s,'// &
          'frain_kg_m2_s,evap_kg_m2_s,fpond_m_per_step,dpnd_flush_m_per_step,'// &
          'dpnd_expon_m_per_step,dpnd_freebd_m_per_step,dpnd_initial_m_per_step,'// &
          'dpnd_dlid_m_per_step,dpnd_melt_m_per_step,dpnd_ridge_m_per_step,'// &
          'pond_initial_m,pond_final_m,aice_initial_fraction,aice_final_fraction,'// &
          'rhofresh_kg_m3,frazil_water_kg_m2,frazil_correction_salt_source_kg_m2,'// &
          'sss_ppt,new_frazil_salinity_ppt,fresh_before_kg_m2_s,fresh_after_kg_m2_s,'// &
          'surface_temperature_C,basal_congelation_m,basal_melt_m,ice_volume_m,'// &
          'sst_C,freezing_temperature_C'
      call check_io(status)
      open(newunit=layer_unit,file='mushy_layers.csv',status='replace',action='write',iostat=status)
      call check_io(status)
      write(layer_unit,'(a)',iostat=status) &
          'step,cell,category,layer,aicen_fraction,vicen_m,vsnon_m,q_J_m3,sice_ppt,temperature_C,phi'
      call check_io(status)
      open(newunit=snow_unit,file='mushy_snow.csv',status='replace',action='write',iostat=status)
      call check_io(status)
      write(snow_unit,'(a)',iostat=status) 'step,cell,category,layer,q_J_m3,temperature_C'
      call check_io(status)
      open(newunit=energy_unit,file='mushy_energy.csv',status='replace',action='write',iostat=status)
      call check_io(status)
      write(energy_unit,'(a)',iostat=status) &
          'step,cell,category,dt_s,initial_energy_J_m2,final_energy_J_m2,'// &
          'input_energy_J_m2,residual_J_m2,residual_W_m2'
      call check_io(status)
      opened = .true.
      end subroutine open_outputs

      subroutine inventory(values)
      real (kind=dbl_kind), intent(out) :: values(5,nx)
      real (kind=dbl_kind) :: rhoi, rhos, rhofresh
      integer :: i, n, k, nt_qice, nt_qsno, nt_sice, nt_apnd, nt_hpnd
      logical (kind=log_kind) :: tr_pond_topo
      call icepack_query_parameters(rhoi_out=rhoi,rhos_out=rhos,rhofresh_out=rhofresh)
      call check_physics()
      call icepack_query_tracer_indices(nt_qice_out=nt_qice,nt_qsno_out=nt_qsno, &
          nt_sice_out=nt_sice,nt_apnd_out=nt_apnd,nt_hpnd_out=nt_hpnd)
      call check_physics()
      call icepack_query_tracer_flags(tr_pond_topo_out=tr_pond_topo)
      call check_physics()
      values = 0.0_dbl_kind
      do i=1,nx
         do n=1,ncat
            values(1,i) = values(1,i) + rhoi*vicen(i,n) + rhos*vsnon(i,n)
            do k=1,nilyr
               values(2,i) = values(2,i) + rhoi*0.001_dbl_kind* &
                    trcrn(i,nt_sice+k-1,n)*vicen(i,n)/real(nilyr,dbl_kind)
               values(3,i) = values(3,i) + &
                    trcrn(i,nt_qice+k-1,n)*vicen(i,n)/real(nilyr,dbl_kind)
            enddo
            do k=1,nslyr
               values(3,i) = values(3,i) + &
                    trcrn(i,nt_qsno+k-1,n)*vsnon(i,n)/real(nslyr,dbl_kind)
            enddo
            if (tr_pond_topo) values(4,i) = values(4,i) + &
                 aicen(i,n)*trcrn(i,nt_apnd,n)*trcrn(i,nt_hpnd,n)
         enddo
         ! The tracked pond inventory is topographic; other pond budgets are
         ! deliberately not advertised as closed by this diagnostic.
         values(1,i) = values(1,i) + rhofresh*values(4,i)
         values(5,i) = aice(i)
      enddo
      end subroutine inventory

      subroutine mushy_begin()
      call open_outputs()
      call inventory(initial)
      end subroutine mushy_begin

      subroutine mushy_before_coupling()
      raw_pond = fpond
      fresh_before = fresh
      end subroutine mushy_before_coupling

      subroutine mushy_write_energy(cell,values)
      integer, intent(in) :: cell
      real (kind=dbl_kind), intent(in) :: values(6,ncat)
      integer :: n, status
      if (.not. mushy_sample()) return
      if (mod(istep,mushy_profilefreq) /= 0) return
      call open_outputs()
      do n=1,ncat
         if (values(1,n) <= 0.0_dbl_kind) cycle
         write(energy_unit,'(*(g0,:,","))',iostat=status) istep,cell,n,values(:,n)
         call check_io(status)
      enddo
      flush(energy_unit,iostat=status)
      call check_io(status)
      end subroutine mushy_write_energy

      subroutine mushy_end()
      real (kind=dbl_kind) :: final(5,nx), rhoi, rhofresh, delta, new_salt, &
          temperature, phi, q, salt, puny
      integer :: i, n, k, nt_qice, nt_sice, nt_qsno, nt_Tsfc, status
      call inventory(final)
      call icepack_query_parameters(rhoi_out=rhoi,rhofresh_out=rhofresh, &
          dSin0_frazil_out=delta,puny_out=puny)
      call check_physics()
      call icepack_query_tracer_indices(nt_qice_out=nt_qice,nt_sice_out=nt_sice, &
          nt_qsno_out=nt_qsno,nt_Tsfc_out=nt_Tsfc)
      call check_physics()
      do i=1,nx
         if (sss(i) > 2.0_dbl_kind*delta) then
            new_salt = sss(i)-delta
         else
            new_salt = sss(i)**2/(4.0_dbl_kind*delta)
         endif
         write(column_unit,'(*(g0,:,","))',iostat=status) istep,i,l_mpond_fresh,dt, &
             initial(1,i),final(1,i),initial(2,i),final(2,i),initial(3,i),final(3,i), &
             fresh_ai(i),fsalt_ai(i),fhocn_ai(i),fsnow(i),frain(i),evap(i),raw_pond(i), &
             dpnd_flush(i),dpnd_expon(i),dpnd_freebd(i),dpnd_initial(i), &
             dpnd_dlid(i),dpnd_melt(i),dpnd_ridge(i),initial(4,i),final(4,i), &
             initial(5,i),final(5,i),rhofresh,rhoi*(frazil(i)-frazil_diag(i)), &
             rhoi*(frazil(i)-frazil_diag(i))*new_salt*0.001_dbl_kind,sss(i),new_salt, &
             fresh_before(i),fresh(i),trcr(i,nt_Tsfc),congel(i),meltb(i),vice(i),sst(i),Tf(i)
         call check_io(status)
         if (mod(istep,mushy_profilefreq) /= 0) cycle
         do n=1,ncat
            if (aicen(i,n) <= puny .or. vicen(i,n) <= 0.0_dbl_kind) cycle
            do k=1,nilyr
               q = trcrn(i,nt_qice+k-1,n)
               salt = trcrn(i,nt_sice+k-1,n)
               call icepack_mushy_state(zqin=q,zSin=salt,zTin=temperature,phi=phi)
               call check_physics()
               write(layer_unit,'(*(g0,:,","))',iostat=status) istep,i,n,k, &
                   aicen(i,n),vicen(i,n),vsnon(i,n),q,salt,temperature,phi
               call check_io(status)
            enddo
            do k=1,nslyr
               q = trcrn(i,nt_qsno+k-1,n)
               temperature = icepack_snow_temperature(qin=q)
               call check_physics()
               write(snow_unit,'(*(g0,:,","))',iostat=status) istep,i,n,k,q,temperature
               call check_io(status)
            enddo
         enddo
      enddo
      flush(column_unit,iostat=status)
      call check_io(status)
      flush(layer_unit,iostat=status)
      call check_io(status)
      flush(snow_unit,iostat=status)
      call check_io(status)
      end subroutine mushy_end

      end module icedrv_mushy_diagnostics
