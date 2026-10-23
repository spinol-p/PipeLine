// Fixture de la regla nestjs-insecure-guard-bypass: NINGUN controlador de este archivo debe generar hallazgo.
import { Controller, Delete, Get, HttpCode, HttpStatus, Param, UseGuards } from '@nestjs/common';
import { Roles } from './roles.decorator';
import { AuthGuard, RolesGuard } from './guards';

/** Guard a nivel de clase, declarado DESPUES de @Controller (caso de users.controller.ts). */
@Controller('api/v1/admin/users')
@UseGuards(AuthGuard, RolesGuard)
@Roles('ADMIN')
export class GuardAfterController {
  @Get()
  list() {
    return [];
  }

  @Delete(':username')
  @HttpCode(HttpStatus.NO_CONTENT)
  async remove(@Param('username') username: string): Promise<void> {
    await Promise.resolve(username);
  }
}

/** Guard a nivel de clase, declarado ANTES de @Controller. */
@UseGuards(AuthGuard)
@Controller('api/v1/admin/lots')
export class GuardBeforeController {
  @Delete(':id')
  remove(@Param('id') id: string) {
    return id;
  }
}

/** Guard a nivel de metodo, antes y despues del decorador de ruta. */
@Controller('api/v1/admin/berths')
export class GuardOnMethodController {
  @Get()
  list() {
    return [];
  }

  @UseGuards(AuthGuard, RolesGuard)
  @Delete(':id')
  removeGuardFirst(@Param('id') id: string) {
    return id;
  }

  @Delete('other/:id')
  @HttpCode(HttpStatus.NO_CONTENT)
  @UseGuards(AuthGuard)
  async removeGuardLast(@Param('id') id: string): Promise<void> {
    await Promise.resolve(id);
  }
}

/** Controlador sin rutas sensibles: no aplica la regla. */
@Controller('health')
export class PublicReadOnlyController {
  @Get()
  status() {
    return { status: 'ok' };
  }
}
