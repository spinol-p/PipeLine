// Fixture de la regla nestjs-insecure-guard-bypass: cada @Delete marcado con "HALLAZGO" debe generar un hallazgo.
import { Controller, Delete, Get, HttpCode, HttpStatus, Param, UseGuards } from '@nestjs/common';
import { AuthGuard } from './guards';

/** Sin ningun guard, ni en la clase ni en el metodo. */
@Controller('api/v1/admin/users')
export class NoGuardController {
  @Get()
  list() {
    return [];
  }

  // HALLAZGO 1
  @Delete(':username')
  @HttpCode(HttpStatus.NO_CONTENT)
  async remove(@Param('username') username: string): Promise<void> {
    await Promise.resolve(username);
  }
}

/** El guard protege otra ruta, no la sensible. */
@Controller('api/v1/admin/lots')
export class GuardOnOtherMethodController {
  @UseGuards(AuthGuard)
  @Get()
  list() {
    return [];
  }

  // HALLAZGO 2
  @Delete(':id')
  remove(@Param('id') id: string) {
    return id;
  }
}
